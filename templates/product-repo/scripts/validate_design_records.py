#!/usr/bin/env python3
"""Validate a product's design record inventory (stdlib only, offline; never calls Figma).

Usage: python scripts/validate_design_records.py [INVENTORY]   (default: design/screens.yml)

Every inventoried screen must record the approved Figma design it is built from:

    required_states: [default, loading, empty, error]   # optional; this is the default
    screens:
      - id: S1                                           # unique
        name: Sign in
        figma_url: https://www.figma.com/design/FILEKEY/Product?node-id=12-34
        node_id: "12:34"                                 # frame id, "12:34" or "12-34"
        version: "2026-11-11 v3"                         # Figma version id/label or date
        states: [default, loading, error]                # states designed in Figma
        not_applicable: [empty]                          # required states that do not apply

Checks: the file parses, `screens` is a non-empty list, each screen has a unique `id`, a
`name`, a Figma `figma_url`, a well-formed `node_id`, a `version`, and covers every required
state in `states` or `not_applicable`. If the inventory file does not exist yet (no design
phase has run) the validator reports that and passes; an existing but invalid file fails.

The file must use the small YAML subset shown above (block mappings, block lists, inline
`[a, b]` lists, quoted or plain scalars, `#` comments) so that CI needs no third-party packages.
"""
from pathlib import Path
import re
import sys

DEFAULT_INVENTORY = Path("design") / "screens.yml"
DEFAULT_REQUIRED_STATES = ["default", "loading", "empty", "error"]
REQUIRED_FIELDS = ["id", "name", "figma_url", "node_id", "version", "states"]
FIGMA_URL_RE = re.compile(r"https://(?:www\.)?figma\.com/(?:file|design|proto|board)/[A-Za-z0-9]+(?:[/?#]\S*)?")
NODE_ID_RE = re.compile(r"\d+[:-]\d+")


class ParseError(ValueError):
    pass


# --- minimal YAML-subset parser -------------------------------------------------------------

def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _scalar(text: str, lineno: int):
    text = text.strip()
    if text == "":
        return None
    if text[0] in "\"'":
        if len(text) < 2 or text[-1] != text[0]:
            raise ParseError(f"line {lineno}: unterminated quoted string")
        return text[1:-1]
    if text.startswith("["):
        if not text.endswith("]"):
            raise ParseError(f"line {lineno}: unterminated inline list")
        inner = text[1:-1].strip()
        return [] if not inner else [_scalar(part, lineno) for part in inner.split(",")]
    if text.startswith(("{", "&", "*", "|", ">", "!")):
        raise ParseError(f"line {lineno}: unsupported YAML syntax {text[0]!r}")
    return text


def _split_key(text: str, lineno: int):
    m = re.match(r"""^("[^"]*"|'[^']*'|[^:\s][^:]*?):(?:\s+(.*))?$""", text)
    if not m:
        return None
    key = m.group(1).strip("\"'")
    return key, m.group(2) or ""


def _tokens(text: str):
    out = []
    for lineno, raw in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ParseError(f"line {lineno}: tabs are not allowed for indentation")
        line = _strip_comment(raw)
        if not line.strip() or line.strip() in ("---", "..."):
            continue
        out.append((len(line) - len(line.lstrip(" ")), line.strip(), lineno))
    return out


def _parse_block(tokens, pos: int, indent: int):
    """Parse the block starting at tokens[pos] whose lines are indented exactly `indent`."""
    if tokens[pos][1].startswith("- ") or tokens[pos][1] == "-":
        return _parse_list(tokens, pos, indent)
    return _parse_map(tokens, pos, indent)


def _parse_value(tokens, pos: int, parent_indent: int, rest: str, lineno: int):
    if rest.strip():
        return _scalar(rest, lineno), pos
    if pos < len(tokens) and tokens[pos][0] > parent_indent:
        return _parse_block(tokens, pos, tokens[pos][0])
    if pos < len(tokens) and tokens[pos][0] == parent_indent and tokens[pos][1].startswith("-"):
        return _parse_list(tokens, pos, parent_indent)  # "key:\n- item" at the same indent
    return None, pos


def _parse_map(tokens, pos: int, indent: int):
    result = {}
    while pos < len(tokens):
        ind, text, lineno = tokens[pos]
        if ind < indent:
            break
        if ind > indent:
            raise ParseError(f"line {lineno}: unexpected indentation")
        if text.startswith("-"):
            break
        split = _split_key(text, lineno)
        if split is None:
            raise ParseError(f"line {lineno}: expected 'key: value'")
        key, rest = split
        if key in result:
            raise ParseError(f"line {lineno}: duplicate key {key!r}")
        result[key], pos = _parse_value(tokens, pos + 1, indent, rest, lineno)
    return result, pos


def _parse_list(tokens, pos: int, indent: int):
    result = []
    while pos < len(tokens):
        ind, text, lineno = tokens[pos]
        if ind < indent or not (text == "-" or text.startswith("- ")):
            if ind > indent:
                raise ParseError(f"line {lineno}: unexpected indentation")
            break
        if ind > indent:
            raise ParseError(f"line {lineno}: unexpected indentation")
        body = text[1:].lstrip()
        if not body:
            value, pos = _parse_value(tokens, pos + 1, indent, "", lineno)
            result.append(value)
            continue
        item_indent = ind + (len(text) - len(body))
        if _split_key(body, lineno) is not None and not body.startswith(("\"", "'", "[")):
            # "- key: value" starts an inline mapping; continue it with following lines.
            sub = [(item_indent, body, lineno)]
            j = pos + 1
            while j < len(tokens) and tokens[j][0] > ind:
                sub.append(tokens[j])
                j += 1
            value, used = _parse_map(sub, 0, item_indent)
            if used != len(sub):
                raise ParseError(f"line {sub[used][2]}: unexpected indentation")
            result.append(value)
            pos = j
        else:
            result.append(_scalar(body, lineno))
            pos += 1
    return result, pos


def parse(text: str):
    tokens = _tokens(text)
    if not tokens:
        return {}
    if tokens[0][0] != 0:
        raise ParseError(f"line {tokens[0][2]}: top level must not be indented")
    value, pos = _parse_block(tokens, 0, 0)
    if pos != len(tokens):
        raise ParseError(f"line {tokens[pos][2]}: unexpected content")
    return value


# --- validation -----------------------------------------------------------------------------

def _str_list(value) -> list | None:
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(v, str) and v for v in value):
        return value
    return None


def validate_data(data) -> list[str]:
    if not isinstance(data, dict):
        return ["inventory must be a mapping with a 'screens' list"]
    errors = []
    required_states = data.get("required_states", DEFAULT_REQUIRED_STATES)
    if _str_list(required_states) is None:
        errors.append("required_states must be a list of state names")
        required_states = DEFAULT_REQUIRED_STATES
    screens = data.get("screens")
    if not isinstance(screens, list) or not screens:
        return errors + ["screens must be a non-empty list"]

    seen = set()
    for index, screen in enumerate(screens, 1):
        if not isinstance(screen, dict):
            errors.append(f"screen #{index}: must be a mapping")
            continue
        sid = screen.get("id")
        label = f"screen {sid}" if isinstance(sid, str) and sid else f"screen #{index}"
        for field in REQUIRED_FIELDS:
            value = screen.get(field)
            if value is None or value == "" or value == []:
                errors.append(f"{label}: missing {field}")
        if isinstance(sid, str) and sid:
            if sid in seen:
                errors.append(f"{label}: duplicate id")
            seen.add(sid)
        for field in ["id", "name", "figma_url", "node_id", "version"]:
            value = screen.get(field)
            if value not in (None, "") and not isinstance(value, str):
                errors.append(f"{label}: {field} must be a single value")
        url = screen.get("figma_url")
        if isinstance(url, str) and url and not FIGMA_URL_RE.fullmatch(url):
            errors.append(f"{label}: figma_url is not a Figma file/design URL: {url}")
        node = screen.get("node_id")
        if isinstance(node, str) and node and not NODE_ID_RE.fullmatch(node):
            errors.append(f"{label}: node_id must look like '12:34' (quote it in YAML), got {node!r}")
        states = _str_list(screen.get("states"))
        not_applicable = _str_list(screen.get("not_applicable"))
        if states is None:
            errors.append(f"{label}: states must be a list of state names")
            states = []
        if not_applicable is None:
            errors.append(f"{label}: not_applicable must be a list of state names")
            not_applicable = []
        if screen.get("states"):
            covered = set(states) | set(not_applicable)
            for state in required_states:
                if state not in covered:
                    errors.append(f"{label}: missing state {state!r} (add to states or not_applicable)")
    return errors


def validate(path: Path) -> list[str]:
    try:
        data = parse(Path(path).read_text(encoding="utf-8"))
    except ParseError as exc:
        return [f"{path}: {exc}"]
    except UnicodeDecodeError:
        return [f"{path}: not UTF-8 text"]
    return [f"{path}: {error}" for error in validate_data(data)]


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_INVENTORY
    if not path.is_file():
        print(f"No design inventory at {path}; nothing to validate")
        return 0
    errors = validate(path)
    if errors:
        print("\n".join(errors))
        return 1
    count = len(parse(path.read_text(encoding="utf-8"))["screens"])
    print(f"Design records valid ({count} screens)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""Mirror canonical skills to Codex, Claude and plugin-source paths."""
from pathlib import Path
import argparse, shutil
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "skills"
DESTS = [ROOT / ".agents/skills", ROOT / ".claude/skills", ROOT / "chatgpt-plugin/skills"]
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    sources = sorted(SRC.glob("*/SKILL.md"))
    if not sources:
        print("No skills found")
        return 1
    errors = []
    for source in sources:
        relative = source.relative_to(SRC)
        for dest in DESTS:
            target = dest / relative
            if args.check:
                if not target.is_file() or target.read_bytes() != source.read_bytes():
                    errors.append(str(target.relative_to(ROOT)))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                print("synced", target.relative_to(ROOT))
    if errors:
        print("Skill mirrors differ:\n" + "\n".join(errors))
        return 1
    print(f"{'Validated' if args.check else 'Synced'} {len(sources)} skills")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())

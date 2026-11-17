// Ship48 in-product "Feedback" link (ADR 0012). Stack-agnostic: plain DOM, no framework.
//
// Two feedback paths; pick one per product with public (non-secret) env values:
//
//   1. GitHub issue form (testers have GitHub accounts and access to the private repo):
//        PUBLIC_FEEDBACK_REPO=<owner>/<repo>
//      The link opens .github/ISSUE_TEMPLATE/feedback.yml, which files an issue labelled
//      type:feedback in the product repository.
//   2. Provider survey (public beta users without GitHub accounts):
//        PUBLIC_FEEDBACK_URL=https://<survey-provider>/<form>
//      Configure the survey with the same three questions (rating 1-5, what were you trying
//      to do, what happened) and no personal-data fields. Triage answers into type:feedback
//      issues by hand. PUBLIC_FEEDBACK_URL wins when both are set.
//
// Neither set, or an invalid value: no link is rendered. Rollback = unset the env values.
// The link never carries user data: nothing is appended to the configured URL, and it opens
// with rel="noopener noreferrer" so the product URL is not sent as a referrer.
//
// Usage:
//   import { createFeedbackLink } from './FeedbackLink.js';
//   const link = createFeedbackLink(document, {
//     surveyUrl: import.meta.env.PUBLIC_FEEDBACK_URL,
//     repo: import.meta.env.PUBLIC_FEEDBACK_REPO,
//   });
//   if (link) footer.append(link);

export const DEFAULT_GITHUB_HOST = 'https://github.com';
export const FEEDBACK_FORM = 'feedback.yml';
export const DEFAULT_LABEL = 'Feedback';

const REPO_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9._-]{1,100}$/;

function clean(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function httpsUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

// Returns the feedback URL, or null when nothing (valid) is configured.
export function feedbackUrl({ surveyUrl, repo, githubHost = DEFAULT_GITHUB_HOST } = {}) {
  const survey = clean(surveyUrl);
  if (survey) return httpsUrl(survey);
  const slug = clean(repo);
  if (!slug || !REPO_RE.test(slug) || slug.split('/')[1].startsWith('.')) return null;
  const host = httpsUrl(clean(githubHost));
  if (!host) return null;
  return `${host.replace(/\/+$/, '')}/${slug}/issues/new?template=${FEEDBACK_FORM}`;
}

// Returns an <a> element, or null when no feedback URL is configured.
export function createFeedbackLink(doc, { label = DEFAULT_LABEL, className, ...config } = {}) {
  const href = feedbackUrl(config);
  if (!href || !doc || typeof doc.createElement !== 'function') return null;
  const link = doc.createElement('a');
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = label;
  if (className) link.className = className;
  return link;
}

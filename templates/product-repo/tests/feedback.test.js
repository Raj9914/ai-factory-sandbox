import { test } from 'node:test';
import assert from 'node:assert/strict';
import { feedbackUrl, createFeedbackLink, FEEDBACK_FORM, DEFAULT_GITHUB_HOST } from '../src/FeedbackLink.js';

const FORM_URL = `${DEFAULT_GITHUB_HOST}/acme/widget/issues/new?template=${FEEDBACK_FORM}`;

function fakeDocument() {
  return { createElement: (tag) => ({ tagName: tag.toUpperCase() }) };
}

test('repo setting points to the feedback issue form', () => {
  assert.equal(DEFAULT_GITHUB_HOST, 'https://github.com');
  assert.equal(feedbackUrl({ repo: 'acme/widget' }), FORM_URL);
  assert.ok(FORM_URL.endsWith('/issues/new?template=feedback.yml'));
  assert.equal(feedbackUrl({ repo: ' acme/widget ', githubHost: 'https://git.example.com/' }),
    'https://git.example.com/acme/widget/issues/new?template=feedback.yml');
});

test('survey URL from env wins over the issue form', () => {
  assert.equal(feedbackUrl({ surveyUrl: 'https://forms.example.com/f/abc', repo: 'acme/widget' }),
    'https://forms.example.com/f/abc');
});

test('nothing configured or invalid values render no link', () => {
  for (const config of [undefined, {}, { surveyUrl: '', repo: '' }, { surveyUrl: '   ' },
    { surveyUrl: 'javascript:alert(1)' }, { surveyUrl: 'http://forms.example.com/f' },
    { surveyUrl: 'https://user:pw@forms.example.com/f' }, { surveyUrl: 'not a url' },
    { repo: 'acme' }, { repo: 'acme/widget/extra' }, { repo: '../evil' }, { repo: 'acme/..' },
    { repo: 'acme/widget', githubHost: 'javascript:x' }]) {
    assert.equal(feedbackUrl(config), null, JSON.stringify(config));
    assert.equal(createFeedbackLink(fakeDocument(), config), null);
  }
});

test('link element opens the form in a new tab without referrer or user data', () => {
  const link = createFeedbackLink(fakeDocument(), { repo: 'acme/widget', className: 'footer-link' });
  assert.equal(link.tagName, 'A');
  assert.equal(link.href, FORM_URL);
  assert.equal(link.textContent, 'Feedback');
  assert.equal(link.target, '_blank');
  assert.equal(link.rel, 'noopener noreferrer');
  assert.equal(link.className, 'footer-link');
});

test('custom label and survey path', () => {
  const link = createFeedbackLink(fakeDocument(), { surveyUrl: 'https://forms.example.com/f/abc', label: 'Send feedback' });
  assert.equal(link.href, 'https://forms.example.com/f/abc');
  assert.equal(link.textContent, 'Send feedback');
});

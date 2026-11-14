// What a document can and cannot put on the page. These are the page's safety properties:
// a failure here is a way for a document to act on the person reading it.
import { expect, test } from 'bun:test';
import { block, imageAllowed, line, linkAllowed } from './markdown';

test('raw HTML in a document is shown as text', () => {
	const html = block('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\nhello <b onclick="x()">there</b>');
	expect(html).not.toContain('<script');
	expect(html).not.toContain('<img');
	expect(html).not.toContain('<b ');
	expect(html).toContain('&lt;script&gt;');
});

test('a link is kept for the web, mail, the page and files beside it', () => {
	for (const href of ['https://example.org/a', 'http://example.org', 'mailto:a@example.org', '#top', 'notes/a.md', './a.md']) {
		expect(linkAllowed(href)).toBe(true);
	}
});

test('a link with any other address is refused', () => {
	for (const href of [
		'javascript:alert(1)',
		'JaVaScRiPt:alert(1)',
		'data:text/html,<script>alert(1)</script>',
		'vbscript:x',
		'file:///etc/passwd',
		'/etc/passwd',
		'//evil.example/x',
		'\\\\evil.example\\x',
		'ftp://example.org',
		''
	]) {
		expect(linkAllowed(href)).toBe(false);
	}
});

test('a refused link is shown as its words, with no anchor', () => {
	const html = block('[click me](/etc/passwd) and [this](//evil.example/x)');
	expect(html).not.toContain('<a');
	expect(html).toContain('click me');
	expect(html).toContain('this');
});

test('a script address never reaches an href', () => {
	const html = block('[a](javascript:alert(1)) [b](JAVASCRIPT:alert(1)) [c](data:text/html;base64,PHNjcmlwdD4=)');
	expect(html).not.toContain('href=');
});

test('a link to the web is marked so the new page cannot reach this one', () => {
	expect(block('[site](https://example.org)')).toContain('rel="noopener noreferrer"');
});

test('a link to another document points into the page', () => {
	const html = block('See [the rules](policies.md) and [positions](./positions.md).');
	expect(html).toContain('href="#policies" data-doc="policies"');
	expect(html).toContain('href="#positions" data-doc="positions"');
});

test('an address that names a built-in property is an ordinary relative link', () => {
	for (const name of ['constructor', '__proto__', 'toString', 'hasOwnProperty']) {
		const html = line(`[x](${name})`);
		expect(html).toContain(`href="${name}"`);
		expect(html).not.toContain('data-doc');
	}
});

test('an image is shown only from beside the page or from the page itself', () => {
	expect(imageAllowed('diagram.png')).toBe(true);
	expect(imageAllowed('data:image/png;base64,AAAA')).toBe(true);
	for (const src of ['https://tracker.example/p.gif', '//tracker.example/p.gif', '/abs.png', 'data:text/html,x', 'javascript:x']) {
		expect(imageAllowed(src)).toBe(false);
	}
});

test('a refused image is shown as its description', () => {
	const html = block('![the chart](https://tracker.example/p.gif)');
	expect(html).not.toContain('<img');
	expect(html).toContain('the chart');
});

test('headings move down under the page own headings, and stop at h6', () => {
	expect(block('### Three', 2)).toContain('<h5>');
	expect(block('###### Six', 2)).toContain('<h6>');
});

test('a table scrolls in its own box', () => {
	expect(block('| a | b |\n|---|---|\n| 1 | 2 |')).toContain('<div class="table"><table>');
});

test('one line renders without a paragraph and holds the same rules', () => {
	const html = line('Merges **nothing** <i>x</i> [bad](javascript:x)');
	expect(html).toContain('<strong>nothing</strong>');
	expect(html).not.toContain('<p>');
	expect(html).not.toContain('<i>');
	expect(html).not.toContain('href=');
});

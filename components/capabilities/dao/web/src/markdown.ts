// Markdown from a project's documents, as HTML that cannot act.
//
// The documents are written by agents and contributors, and the page is opened by a person.
// So raw HTML in the Markdown is shown as text, and a link or an image is kept only when its
// address is one of a short list of kinds. Anything else is shown as its words.

import MarkdownIt, { type Token } from 'markdown-it';

/** The documents a link may name, and the key of the section that shows each. */
export const DOCUMENTS = new Map<string, string>(Object.entries({
	'constitution.md': 'constitution',
	'policies.md': 'policies',
	'principles.md': 'principles',
	'positions.md': 'positions',
	'participants.md': 'participants',
	'README.md': 'front-door',
	'book.html': 'front-door'
}));

const SCHEME = /^([a-z][a-z0-9+.-]*):/i;
const LINK_SCHEMES = new Set(['http', 'https', 'mailto']);

/** A path beside the page: no scheme, and not from a root or another host. */
const relative = (address: string): boolean =>
	address !== '' && !address.startsWith('/') && !address.startsWith('\\') && !SCHEME.test(address);

/** Whether a link may be a link: within the page, beside it, or web or mail. */
export function linkAllowed(href: string): boolean {
	const scheme = SCHEME.exec(href);
	if (scheme) return LINK_SCHEMES.has(scheme[1].toLowerCase());
	return href.startsWith('#') || relative(href);
}

/** Whether an image may be shown: a file beside the page, or one carried in the page. */
export function imageAllowed(src: string): boolean {
	return relative(src) || src.toLowerCase().startsWith('data:image/');
}

function inline(children: Token[]): Token[] {
	const out: Token[] = [];
	let dropped = 0; // links whose address is refused and whose closing tag is still to come
	for (let child of children) {
		if (child.type === 'link_open') {
			const href = String(child.attrGet('href') ?? '');
			const doc = DOCUMENTS.get(href.replace(/^\.\//, ''));
			if (doc) {
				child.attrSet('href', `#${doc}`);
				child.attrSet('data-doc', doc);
			} else if (!linkAllowed(href)) {
				dropped += 1;
				continue;
			} else if (SCHEME.test(href)) {
				child.attrSet('rel', 'noopener noreferrer');
			}
		} else if (child.type === 'link_close' && dropped) {
			dropped -= 1;
			continue;
		} else if (child.type === 'image' && !imageAllowed(String(child.attrGet('src') ?? ''))) {
			const words = new (child.constructor as typeof Token)('text', '', 0);
			words.content = child.content;
			child = words;
		}
		out.push(child);
	}
	return out;
}

function settle(tokens: Token[], shift: number): void {
	for (const token of tokens) {
		if (token.type === 'heading_open' || token.type === 'heading_close') {
			token.tag = `h${Math.min(Number(token.tag[1]) + shift, 6)}`;
		}
		if (token.children) token.children = inline(token.children);
	}
}

function make(): InstanceType<typeof MarkdownIt> {
	const md = new MarkdownIt('commonmark', { html: false }).enable('table');
	// A wide table scrolls inside its own box, not the page.
	md.renderer.rules.table_open = () => '<div class="table"><table>\n';
	md.renderer.rules.table_close = () => '</table></div>\n';
	return md;
}

const md = make();

/** A document, or part of one. `shift` moves its headings down under the page's own. */
export function block(text: string, shift = 0): string {
	const tokens = md.parse(text, {});
	settle(tokens, shift);
	return md.renderer.render(tokens, md.options, {});
}

/** One line of a document, such as a table cell: no paragraphs, no headings. */
export function line(text: string): string {
	const tokens = md.parseInline(text, {});
	settle(tokens, 0);
	return md.renderer.render(tokens, md.options, {});
}

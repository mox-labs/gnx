// Text-quote anchoring for annotations: find a quoted span inside rendered
// markdown and wrap it in <mark> elements. Quote + surrounding context
// (W3C-annotation style) keeps anchors stable across small edits; if the
// block text changed enough that the quote is gone, the annotation falls
// back to block level (paint returns false).

interface Seg {
	node: Text;
	start: number;
}

function textMap(root: HTMLElement): { text: string; segs: Seg[] } {
	const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
	let text = '';
	const segs: Seg[] = [];
	while (walker.nextNode()) {
		const node = walker.currentNode as Text;
		segs.push({ node, start: text.length });
		text += node.data;
	}
	return { text, segs };
}

function overlapTail(hay: string, needle: string): number {
	// length of the longest suffix of `hay` that is a suffix of `needle`-aligned match
	let n = 0;
	for (let i = 1; i <= Math.min(hay.length, needle.length); i++) {
		if (hay.slice(-i) === needle.slice(-i)) n = i;
	}
	return n;
}

function overlapHead(hay: string, needle: string): number {
	let n = 0;
	for (let i = 1; i <= Math.min(hay.length, needle.length); i++) {
		if (hay.slice(0, i) === needle.slice(0, i)) n = i;
	}
	return n;
}

/** Global character offsets of a DOM range within root, or null if it isn't a clean text selection. */
export function offsetsOfRange(root: HTMLElement, range: Range): { start: number; end: number } | null {
	const { segs } = textMap(root);
	let start = -1;
	let end = -1;
	for (const { node, start: s } of segs) {
		if (node === range.startContainer) start = s + range.startOffset;
		if (node === range.endContainer) end = s + range.endOffset;
	}
	if (start < 0 || end < 0 || end <= start) return null;
	return { start, end };
}

/** Quote + context for a span of the root's text. */
export function quoteAt(
	root: HTMLElement,
	start: number,
	end: number,
	ctx = 32
): { quote: string; prefix: string; suffix: string } {
	const { text } = textMap(root);
	return {
		quote: text.slice(start, end),
		prefix: text.slice(Math.max(0, start - ctx), start),
		suffix: text.slice(end, end + ctx)
	};
}

export interface PaintOpts {
	quote: string;
	prefix?: string;
	suffix?: string;
	threadId: string;
	resolved: boolean;
	onClick: (threadId: string) => void;
}

/** Wrap the best match for the quote in mark.annot elements. Returns false if not found. */
export function paintQuote(root: HTMLElement, opts: PaintOpts): boolean {
	const { text, segs } = textMap(root);
	if (!opts.quote) return false;

	const candidates: number[] = [];
	let i = -1;
	while ((i = text.indexOf(opts.quote, i + 1)) !== -1) candidates.push(i);
	if (candidates.length === 0) return false;

	let best = candidates[0];
	if (candidates.length > 1) {
		let bestScore = -Infinity;
		for (const c of candidates) {
			let score = 0;
			if (opts.prefix) score += overlapTail(text.slice(Math.max(0, c - opts.prefix.length), c), opts.prefix);
			if (opts.suffix) score += overlapHead(text.slice(c + opts.quote.length), opts.suffix);
			if (score > bestScore) {
				bestScore = score;
				best = c;
			}
		}
	}

	const mStart = best;
	const mEnd = best + opts.quote.length;

	// Wrap every text segment intersecting [mStart, mEnd). Snapshot first: we mutate nodes.
	for (const { node, start } of [...segs]) {
		const nEnd = start + node.data.length;
		const s = Math.max(mStart, start);
		const e = Math.min(mEnd, nEnd);
		if (s >= e) continue;
		if (!node.parentNode) continue;

		let mid = node;
		const localS = s - start;
		const localE = e - start;
		if (localE < node.data.length) node.splitText(localE);
		if (localS > 0) mid = node.splitText(localS);

		const mark = document.createElement('mark');
		mark.className = 'annot' + (opts.resolved ? ' resolved' : '');
		mark.dataset.thread = opts.threadId;
		mark.addEventListener('click', (ev) => {
			ev.stopPropagation();
			opts.onClick(opts.threadId);
		});
		mid.parentNode!.replaceChild(mark, mid);
		mark.appendChild(mid);
	}
	return true;
}

/** Remove all annotation marks under root, restoring the original text nodes. */
export function clearAnnots(root: HTMLElement) {
	for (const m of [...root.querySelectorAll('mark.annot')]) {
		const parent = m.parentNode;
		if (!parent) continue;
		while (m.firstChild) parent.insertBefore(m.firstChild, m);
		parent.removeChild(m);
	}
	root.normalize();
}

// What one reader is looking at: the book, what they have selected, and what they have opened.

import { getContext, setContext } from 'svelte';
import { SvelteSet } from 'svelte/reactivity';
import { edges, neighbourhood, type Book, type Edge, type Selection } from './model';

const KEY = Symbol('dao-book');

/** The three levels of rule, in the order a reader meets them. */
export const RULES = ['constitution', 'policies', 'principles'] as const;

export class Reader {
	readonly book: Book;
	readonly edges: Edge[];
	selected = $state<Selection | null>(null);
	// What the pointer or the keyboard is on. It lights the map without changing the selection.
	hot = $state<Selection | null>(null);
	// The sections a reader has opened, as `<document key>/<index>`.
	readonly open = new SvelteSet<string>();
	// The id of the element to bring into view, and a count so the same id can be asked for twice.
	target = $state<{ id: string; n: number }>({ id: '', n: 0 });

	readonly lit = $derived.by(() => {
		const focus = this.hot ?? this.selected;
		return focus ? neighbourhood(this.edges, focus) : null;
	});

	constructor(book: Book) {
		this.book = book;
		this.edges = edges(book);
	}

	is(s: Selection): boolean {
		return this.selected?.kind === s.kind && this.selected.name === s.name;
	}

	/** Select, or clear when it is already the selection. */
	toggle(s: Selection): void {
		this.selected = this.is(s) ? null : s;
	}

	select(s: Selection): void {
		this.selected = s;
		this.go('detail');
	}

	go(id: string): void {
		this.target = { id, n: this.target.n + 1 };
	}

	/** Open one section of a rule document and bring it into view. */
	showSection(key: string, index: number): void {
		this.open.add(`${key}/${index}`);
		this.go(`s-${key}-${index}`);
	}

	/** Follow a link from one document to another. */
	showDocument(key: string): void {
		if ((RULES as readonly string[]).includes(key)) this.go(`d-${key}`);
		else if (key === 'positions' || key === 'participants') this.go('who');
		else this.go('top');
	}
}

export const provide = (reader: Reader): Reader => setContext(KEY, reader);
export const reader = (): Reader => getContext<Reader>(KEY);

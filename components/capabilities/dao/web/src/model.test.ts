import { expect, test } from 'bun:test';
import { edges, neighbourhood, parse, read, search, sections, type Book } from './model';

const raw = {
	format: 1,
	title: 'How Acme is run',
	directory: 'dao',
	complete: true,
	sources: 'a'.repeat(64),
	documents: [
		{ key: 'constitution', name: 'Constitution', file: 'constitution.md', summary: 's', status: 'PRESENT', gaps: [], markdown: 'Opening.\n\n## Purpose\n\nShip working software.\n\n## Amendment\n\nOnly the owner amends.' },
		{ key: 'policies', name: 'Governing policies', file: 'policies.md', summary: 's', status: 'PARTIAL', gaps: ['thin'], markdown: '## Releases\n\nA release needs a review.' }
	],
	positions: [
		{ name: 'Builder', does: 'Writes the code and its tests.', must_not: 'Merge its own work.', checked_by: ['Reviewer'], held_by: ['claude'], held_only_by_people: false, checks: [] },
		{ name: 'Reviewer', does: 'Reads every change before a merge.', must_not: 'Write the change.', checked_by: ['Owner'], held_by: ['sam'], held_only_by_people: true, checks: ['Builder'] },
		{ name: 'Owner', does: 'Decides what is built.', must_not: '', checked_by: [], held_by: ['sam'], held_only_by_people: true, checks: ['Reviewer'] }
	],
	participants: [
		{ name: 'sam', kind: 'human', positions: ['Reviewer', 'Owner'], defined_in: '' },
		{ name: 'claude', kind: 'agent', positions: ['Builder'], defined_in: '.claude/agents/builder.md' }
	],
	chains: [['Builder', 'Reviewer', 'Owner']]
};
const book: Book = parse(raw);

test('a book of another format is refused, and the message says what to do', () => {
	expect(() => parse({ ...raw, format: 2 })).toThrow(/dao book/);
	expect(() => parse(null)).toThrow();
	expect(() => read('not json')).toThrow(/not JSON/);
});

test('fields of the wrong type are dropped, not trusted', () => {
	const odd = parse({ format: 1, title: 7, documents: 'x', positions: [{ name: 'A', checked_by: [1, 'B', {}] }, { does: 'nameless' }, null], participants: [{ name: 'p', kind: 'robot' }] });
	expect(odd.title).toBe('');
	expect(odd.documents).toEqual([]);
	expect(odd.positions.map((p) => p.name)).toEqual(['A']);
	expect(odd.positions[0].checked_by).toEqual(['B']);
	expect(odd.participants[0].kind).toBeNull();
});

test('the map links holders to positions and work to its checker', () => {
	const ids = edges(book).map((e) => e.id);
	expect(ids).toContain('["holds","sam","Owner"]');
	expect(ids).toContain('["holds","claude","Builder"]');
	expect(ids).toContain('["checks","Builder","Reviewer"]');
	expect(ids).not.toContain('["checks","Owner","Owner"]');
});

test('a name repeated in the documents is shown once, so the page still draws', () => {
	const twice = parse({
		...raw,
		documents: [...raw.documents, raw.documents[0]],
		positions: [...raw.positions, { ...raw.positions[0], checked_by: ['Reviewer', 'Reviewer'] }],
		participants: [...raw.participants, raw.participants[0], { name: 'x', kind: 'agent', positions: ['Builder', 'Builder'] }]
	});
	expect(twice.documents.map((d) => d.key)).toEqual(['constitution', 'policies']);
	expect(twice.positions.map((p) => p.name)).toEqual(['Builder', 'Reviewer', 'Owner']);
	expect(twice.participants.map((p) => p.name)).toEqual(['sam', 'claude', 'x']);
	const ids = edges(twice).map((e) => e.id);
	expect(new Set(ids).size).toBe(ids.length);
});

test('two different links never share an id, whatever the names hold', () => {
	const odd = parse({
		format: 1,
		positions: [{ name: 'c' }, { name: 'b:c' }],
		participants: [
			{ name: 'a:b', kind: 'agent', positions: ['c'] },
			{ name: 'a', kind: 'agent', positions: ['b:c'] }
		]
	});
	const ids = edges(odd).map((e) => e.id);
	expect(ids.length).toBe(2);
	expect(new Set(ids).size).toBe(2);
});

test('one long heading line cannot make a document slow to split', () => {
	const started = performance.now();
	const parts = sections('## a' + ' '.repeat(1_000_000) + 'b ##\n\ntext');
	expect(performance.now() - started).toBeLessThan(500);
	expect(parts.length).toBe(1);
	expect(parts[0].title.startsWith('a')).toBe(true);
	expect(parts[0].title.endsWith('b')).toBe(true);
});

test('a selection lights itself and what is one link away', () => {
	const lit = neighbourhood(edges(book), { kind: 'position', name: 'Reviewer' });
	expect([...lit.nodes].sort()).toEqual(['participant:sam', 'position:Builder', 'position:Owner', 'position:Reviewer']);
	expect(lit.edges.has('h:claude:Builder')).toBe(false);
});

test('a document splits at its second-level headings, not inside a code block', () => {
	const parts = sections('Intro.\n\n## One\n\ntext\n\n```\n## not a heading\n```\n\n## Two ##\n\nmore');
	expect(parts.map((s) => s.title)).toEqual(['', 'One', 'Two']);
	expect(parts[1].body).toContain('## not a heading');
});

test('search finds a word in positions and in the rules, and says where', () => {
	const hits = search(book, 'merge');
	expect(hits.map((h) => `${h.kind}:${h.label}:${h.context}`)).toEqual(['position:Builder:Must not', 'position:Reviewer:Does']);
	const rule = search(book, 'review').find((h) => h.kind === 'section');
	expect(rule).toMatchObject({ target: 'policies', index: 0, label: 'Releases' });
	expect(search(book, 'x')).toEqual([]);
});

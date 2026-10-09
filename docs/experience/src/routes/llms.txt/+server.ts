import { listDocs, getDocSource } from '$lib/server/content';

// gnx's primary reader is an agent. This is the index it fetches first to
// enumerate the doc surface before reading any page (llms.txt convention).
//
// Public register only: an agent reading this mid-task needs usage guidance —
// what works today, how to drive it — not the internal convergence surface.
// (The dossier is the internal design wing; it is deliberately absent here.)

const SUMMARY =
	'gnx is a Claude Code marketplace of composable components: skills and agents install as plugins; capabilities are configuration-driven Python packages. The catalog page lists what exists, read from source.';

// Internal docs (spec/design/build registers) are deliberately absent — they live in
// the dossier wing. If a spec index for agents is wanted later, it's a dossier concern.
//
// Agent-utility order: the runnable path first, then orientation,
// then the command/grammar surface, then the why. (vyasa, ia-two-wings spec)
const PRIORITY = [
	'install-a-plugin',
	'overview',
	'what-is-gnx',
	'how-components-work',
	'cli-reference',
	'compose-components',
	'grammar-reference',
	'gnx-init',
	'why-a-catalog',
	'vendor-neutral-by-structure',
	'the-primary-reader-is-an-agent',
	'api-schema-index',
	'author-a-component'
];

function note(slug: string): string {
	const src = getDocSource(slug) ?? '';
	const line = src
		.split('\n')
		.map((l) => l.trim())
		.find((l) => l && !l.startsWith('#') && !l.startsWith('>') && !l.startsWith('---'));
	return (line ?? '').replace(/[*`]/g, '').replace(/\s+/g, ' ').slice(0, 150);
}

export function GET() {
	const pub = listDocs().filter((d) => d.register === 'public');
	const rank = (slug: string) => {
		const i = PRIORITY.indexOf(slug);
		return i === -1 ? PRIORITY.length : i;
	};
	const docs = [...pub].sort((a, b) => rank(a.slug) - rank(b.slug));

	const out: string[] = ['# gnx', '', `> ${SUMMARY}`, '', '## Docs', ''];
	for (const d of docs) {
		const status = d.status ? ` [${d.status}]` : '';
		out.push(`- [${d.title}](/docs/${d.slug})${status}: ${note(d.slug)}`);
	}
	out.push(
		'',
		'Markdown pages have a clean twin at /raw/<slug> (e.g. /raw/what-is-gnx). Fetch this index first; read on demand.',
		''
	);
	return new Response(out.join('\n'), {
		headers: { 'content-type': 'text/plain; charset=utf-8' }
	});
}

// static builds ship this file too (D22)
export const prerender = true;

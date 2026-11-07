import { base } from '$app/paths';
import { listDocs, getDocSource } from '$lib/server/content';
import { loadCatalog } from '$lib/server/catalog';
import { MARKETPLACE_REF, REPO_URL } from '$lib/catalog';
import { GOALS } from '$lib/goals';

// gnx's primary reader is an agent, often one handed this site and asked "what is this, and
// why would I use it?". This is the index it fetches first (llms.txt convention): the answer
// up top, then every page with its markdown twin, read on demand.
//
// Public register only: an agent reading this mid-task needs usage guidance —
// what works today, how to drive it — not the internal convergence surface.
// (The dossier is the internal design wing; it is deliberately absent here.)
//
// Components and plugins come from the catalog loader, so this index lists what the catalog
// page lists. Links carry the deploy base: the site is served under a path, and a
// root-relative link without it lands outside the site.

const SUMMARY =
	'gnx: components that make agents more effective, and their work inspectable. Python packages installed with uv, and skills installed into Claude Code as plugins.';

const WHAT = [
	`Capabilities are Python packages, installed with \`uv\` from ${REPO_URL}. None is on a package index: the PyPI names \`matrix\` and \`ix\` belong to other projects. Each package's README has its install line.`,
	`Skills install into Claude Code as plugins: \`/plugin marketplace add ${MARKETPLACE_REF}\`, then \`/plugin install <plugin>@gnx\`.`,
	'`ix --skill`, `recon --skill`, `matrix --skill` and `hardline --skill` print the guide for the installed version. Read it before driving the tool.',
	'matrix, ix and recon plan a run before it starts and record it as it goes; `ix plan` and `recon survey --dry-run` show each setting and where it came from.'
];

// Agent-utility order for the docs: the runnable path first, then orientation, then the why.
const PRIORITY = ['install-a-plugin', 'what-is-gnx', 'how-gnx-is-built'];

/** A one-line description ends as a sentence, so what follows it reads as separate. */
const sentence = (s: string) =>
	!s || /[.!?]$/.test(s) ? s : `${s.replace(/[:;,]$/, '')}.`;

/** First prose line of a doc, as plain text. */
function note(slug: string): string {
	const src = getDocSource(slug) ?? '';
	const line = src
		.split('\n')
		.map((l) => l.trim())
		.find((l) => l && !/^(#|>|---|```|\||-|\*|\d+\.)/.test(l));
	return (line ?? '')
		.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
		.replace(/[*`]/g, '')
		.replace(/\s+/g, ' ')
		.slice(0, 160);
}

export function GET() {
	const { entries, plugins } = loadCatalog();
	const pub = listDocs().filter((d) => d.register === 'public');
	const rank = (slug: string) => {
		const i = PRIORITY.indexOf(slug);
		return i === -1 ? PRIORITY.length : i;
	};
	const docs = [...pub].sort((a, b) => rank(a.slug) - rank(b.slug));

	const out: string[] = ['# gnx', '', `> ${SUMMARY}`, '', '## What it is', ''];
	for (const w of WHAT) out.push(`- ${w}`);

	out.push('', '## Why you would use it: start by goal', '');
	for (const g of GOALS) out.push(`- ${g.goal}: [${g.name}](${base}${g.path})`);

	out.push('', '## Components', '');
	for (const e of entries) {
		const desc = e.description ? `: ${sentence(e.description)}` : '.';
		out.push(
			`- [${e.slug}](${base}/components/${e.slug}) (${e.kind})${desc} Markdown: ${base}/components/${e.slug}.md`
		);
	}

	out.push('', '## Plugins', '');
	for (const p of plugins) {
		const members = p.members.map((m) => m.name).join(', ');
		out.push(
			`- [${p.name}](${base}/plugins/${p.name}): ${sentence(p.description)} Install: \`/plugin install ${p.name}@gnx\`. Bundles: ${members}.`
		);
	}

	out.push('', '## Docs', '');
	for (const d of docs) {
		out.push(
			`- [${d.title}](${base}/docs/${d.slug}): ${sentence(note(d.slug))} Markdown: ${base}/docs/${d.slug}.md`
		);
	}

	out.push(
		'',
		`Component and doc pages have a markdown twin at the same path plus \`.md\`. The catalog (${base}/catalog) lists every component and plugin, read from source.`,
		''
	);
	return new Response(out.join('\n'), {
		headers: { 'content-type': 'text/plain; charset=utf-8' }
	});
}

// static builds ship this file too (D22)
export const prerender = true;

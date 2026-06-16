import { listDocs, SECTION_LABEL, getDocSource, type Section } from '$lib/server/content';

// gnx's primary reader is an agent. This is the index it fetches first to
// enumerate the whole doc surface before reading any page (llms.txt convention).

const SUMMARY =
	'gnx is a registry, a Claude Code marketplace, and an agentic CLI of composable cognitive extensions. Your agent composes capability from the catalog; you govern what it composes from. Designed for any agent; Claude Code today. slick (v0.2.0) + the cix plugin family ship; the gnx CLI/registry/marketplace are designed.';

const ORDER: Section[] = ['start', 'guides', 'reference', 'explanation', 'design', 'build'];

function note(slug: string): string {
	const src = getDocSource(slug) ?? '';
	const line = src
		.split('\n')
		.map((l) => l.trim())
		.find((l) => l && !l.startsWith('#') && !l.startsWith('>') && !l.startsWith('---'));
	return (line ?? '').replace(/[*`]/g, '').replace(/\s+/g, ' ').slice(0, 150);
}

export function GET() {
	const docs = listDocs();
	const out: string[] = ['# gnx', '', `> ${SUMMARY}`, ''];
	for (const section of ORDER) {
		const here = docs.filter((d) => d.section === section);
		if (!here.length) continue;
		const label = SECTION_LABEL[section];
		out.push(section === 'design' || section === 'build' ? `## Internal — ${label}` : `## ${label}`, '');
		for (const d of here) {
			const status = d.status ? ` [${d.status}]` : '';
			out.push(`- [${d.title}](/docs/${d.slug})${status}: ${note(d.slug)}`);
		}
		out.push('');
	}
	out.push(
		'Every page has a clean markdown twin at /raw/<slug> (e.g. /raw/what-is-gnx). Fetch this index first; read twins on demand.',
		''
	);
	return new Response(out.join('\n'), {
		headers: { 'content-type': 'text/plain; charset=utf-8' }
	});
}

import { DEPLOY_BASE } from '#lib/base.ts';
import type { EntryGenerator, RequestHandler } from './$types';
import { getDocSource, listDocs } from '#lib/server/content.ts';

// The clean markdown twin for each public doc: /docs/<slug>.md beside /docs/<slug>. What an
// agent reads instead of scraping rendered SvelteKit. Linked from /llms.txt.
//
// Prerendered, so the static build ships it: a twin that exists only on a dev server is a
// link that 404s for every agent that follows it. The `.md` suffix also gives the static file
// a markdown content type.
export const prerender = true;

export const entries: EntryGenerator = () =>
	listDocs()
		.filter((d) => d.register === 'public')
		.map((d) => ({ slug: d.slug }));

export const GET: RequestHandler = ({ params }) => {
	// Only public-register docs are served here; internal dossier docs 404.
	const meta = listDocs().find((d) => d.slug === params.slug);
	if (!meta || meta.register !== 'public') return new Response('not found', { status: 404 });
	const src = getDocSource(params.slug);
	if (src == null) return new Response('not found', { status: 404 });
	// getDocSource strips frontmatter, so prepend the maturity marker the twin would otherwise lose.
	const status = meta.status ?? 'shipped';
	// Site-absolute links get the deploy base, as the rendered page's do.
	const body = `> status: ${status} · canonical: ${DEPLOY_BASE}/docs/${params.slug}\n\n${src.replace(
		/\]\(\/(?!\/)/g,
		`](${DEPLOY_BASE}/`
	)}`;
	return new Response(body, {
		headers: { 'content-type': 'text/markdown; charset=utf-8' }
	});
};

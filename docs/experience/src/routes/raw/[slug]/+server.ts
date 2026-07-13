import { getDocSource, listDocs } from '$lib/server/content';

// The clean markdown twin for each page — what an agent reads instead of
// scraping rendered SvelteKit. Linked from /llms.txt.
export function GET({ params }: { params: { slug: string } }) {
	// Only public-register docs are served here; internal dossier docs 404.
	const meta = listDocs().find((d) => d.slug === params.slug);
	if (!meta || meta.register !== 'public') return new Response('not found', { status: 404 });
	const src = getDocSource(params.slug);
	if (src == null) return new Response('not found', { status: 404 });
	// getDocSource strips frontmatter, so prepend the maturity marker the twin would otherwise lose.
	const status = meta.status ?? 'shipped';
	const body = `> status: ${status} — canonical: /docs/${params.slug}\n\n${src}`;
	return new Response(body, {
		headers: { 'content-type': 'text/markdown; charset=utf-8' }
	});
}

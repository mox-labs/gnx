import { getDocSource } from '$lib/server/content';

// The clean markdown twin for each page — what an agent reads instead of
// scraping rendered SvelteKit. Linked from /llms.txt.
export function GET({ params }: { params: { slug: string } }) {
	const src = getDocSource(params.slug);
	if (src == null) return new Response('not found', { status: 404 });
	return new Response(src, {
		headers: { 'content-type': 'text/markdown; charset=utf-8' }
	});
}

import { base } from '$app/paths';
import type { EntryGenerator, RequestHandler } from './$types';
import { getOverview, loadCatalog } from '$lib/server/catalog';
import { MARKETPLACE_REF, sourceUrl, shippedIn } from '$lib/catalog';

// The markdown twin of /components/<name>: what an agent reads instead of the rendered page.
// Same facts as the page, from the same loader.
export const prerender = true;

export const entries: EntryGenerator = () =>
	loadCatalog().entries.map((e) => ({ name: e.slug }));

export const GET: RequestHandler = ({ params }) => {
	const entry = loadCatalog().entries.find((e) => e.slug === params.name);
	if (!entry) return new Response('not found', { status: 404 });
	const overview = getOverview(entry);

	const ships = shippedIn(entry);
	const install =
		entry.kind === 'Capability'
			? `a Python package, installed with uv; its README has the install line.${
					ships.length ? ` Its skill ships in the plugin(s): ${ships.join(', ')}.` : ''
				}`
			: ships.length
				? `ships in the plugin(s): ${ships.join(', ')}. Install with ${ships
						.map((p) => `\`/plugin install ${p}@gnx\``)
						.join(' or ')}, after \`/plugin marketplace add ${MARKETPLACE_REF}\`.`
				: 'not bundled into a plugin.';

	const summary = overview?.summary ?? entry.description;
	const lines = [
		`# ${overview?.title ?? entry.slug}`,
		'',
		`> ${entry.kind}.${summary ? ` ${summary}` : ''}`,
		'',
		`- Install: ${install}`,
		`- Source: ${sourceUrl(entry.dir)}`,
		...(entry.readme ? [`- README: ${sourceUrl(entry.readme)}`] : []),
		`- Page: ${base}/components/${entry.slug}`,
		''
	];
	if (overview) lines.push(overview.markdown, '');

	return new Response(lines.join('\n'), {
		headers: { 'content-type': 'text/markdown; charset=utf-8' }
	});
};

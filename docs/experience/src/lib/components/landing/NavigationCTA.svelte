<script lang="ts">
	// East panel — the ways in, by what the reader came to do. Each goal opens the component
	// that does it; the catalog and llms.txt follow for a reader who wants the whole set.
	// The dossier is the one discreet seam to the internal register — local only (D22).
	import { base } from '$app/paths';
	import { page } from '$app/state';
	import { GOALS } from '$lib/goals';

	// "How gnx is built" is a content page; link it only once it exists, so the landing never
	// carries a dead link.
	const hasBuilt = $derived(
		(page.data.docs as { slug: string; register: string }[] | undefined)?.some(
			(d) => d.slug === 'how-gnx-is-built' && d.register === 'public'
		) ?? false
	);

	const links = $derived([
		...(hasBuilt ? [{ href: `${base}/docs/how-gnx-is-built`, label: 'How gnx is built' }] : []),
		{ href: `${base}/catalog`, label: 'The catalog' },
		{ href: `${base}/llms.txt`, label: 'For agents: llms.txt' },
		...(page.data.publicOnly ? [] : [{ href: '/dossier', label: 'the dossier · internal', muted: true }])
	]);
</script>

<nav class="cta" aria-labelledby="start-here">
	<h2 id="start-here" class="label">Start here</h2>
	<ul class="goals">
		{#each GOALS as g (g.name)}
			<li>
				<a href="{base}{g.path}">
					<span class="goal">{g.goal}</span>
					<span class="name">{g.name} <span class="arrow" aria-hidden="true">→</span></span>
				</a>
			</li>
		{/each}
	</ul>
	<ul class="more">
		{#each links as l (l.href)}
			<li><a href={l.href} class:muted={l.muted}>{l.label}</a></li>
		{/each}
	</ul>
</nav>

<style>
	.cta {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-family: var(--font-mono);
		text-align: right;
		max-width: 24rem;
	}
	.label {
		font: 600 0.68rem var(--font-sans);
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: var(--muted);
		margin: 0;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.goals a {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		padding: 0.3rem 0;
		text-decoration: none;
		color: var(--muted);
		font-size: 0.82rem;
		line-height: 1.35;
		transition: color 0.12s;
	}
	.goals .name {
		color: var(--ci-green);
		font-size: var(--type-sm);
	}
	.goals a:hover .goal {
		color: var(--fg);
	}
	.goals a:hover .name {
		color: var(--resolved);
	}
	.more {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		margin-top: var(--space-1);
		padding-top: var(--space-1);
		border-top: 1px solid var(--line);
	}
	.more a {
		text-decoration: none;
		color: var(--muted);
		font-size: 0.82rem;
	}
	.more a:hover {
		color: var(--accent);
	}
	.more a.muted {
		font-size: var(--type-xs, 0.72rem);
		opacity: 0.55;
	}
	@media (max-width: 768px) {
		.cta {
			text-align: left;
			max-width: 26rem;
			width: 100%;
		}
		.goals a {
			align-items: flex-start;
		}
	}
</style>

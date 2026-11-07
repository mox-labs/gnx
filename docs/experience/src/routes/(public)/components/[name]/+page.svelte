<script lang="ts">
	import { base } from '$app/paths';
	import { sourceUrl } from '$lib/catalog';

	let { data } = $props();

	const e = $derived(data.entry);
	const title = $derived(data.overview?.title ?? e.slug);
	const summary = $derived(data.overview?.summary ?? e.description);
</script>

<svelte:head>
	<title>{title} · gnx</title>
	{#if summary}<meta name="description" content={summary} />{/if}
	<link rel="alternate" type="text/markdown" href="{base}/components/{e.slug}.md" />
</svelte:head>

<article class="doc component">
	<div class="doc-meta">
		<span class="kind">{e.kind}</span>
	</div>

	<h1>{title}</h1>
	{#if summary}<p class="lede">{summary}</p>{/if}

	<dl class="facts">
		<dt>Install</dt>
		<dd>
			{#if e.kind === 'Capability'}
				a Python package, installed with <code>uv</code>; {#if e.readme}<a href={sourceUrl(e.readme)}
						>its README</a
					>{:else}its README{/if} has the install line.
				{#if data.plugins.length}
					Its skill ships in
					{#each data.plugins as p, i (p.name)}{i ? ', ' : ''}<a href="{base}/plugins/{p.name}"
							><code>{p.name}</code></a
						>{/each}.
				{/if}
			{:else if data.plugins.length}
				ships in
				{#each data.plugins as p, i (p.name)}{i ? ', ' : ''}<a href="{base}/plugins/{p.name}"
						><code>{p.name}</code></a
					>{/each}
			{:else}
				not bundled into a plugin
			{/if}
		</dd>
		<dt>Source</dt>
		<dd>
			<a href={sourceUrl(e.dir)}><code>{e.dir}</code></a>
			{#if e.readme}· <a href={sourceUrl(e.readme)}>README</a>{/if}
			· <a href="{base}/components/{e.slug}.md">markdown</a>
		</dd>
	</dl>

	{#if data.overview}
		<div class="overview">{@html data.overview.html}</div>
	{/if}

	<nav class="pager">
		<a href="{base}/catalog">← The catalog</a>
	</nav>
</article>

<style>
	.doc-meta {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		margin-bottom: 1.5rem;
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.doc-meta .kind {
		opacity: 0.6;
		font-weight: 600;
	}
	.lede {
		color: var(--muted);
	}
	.facts {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 0.35rem 1rem;
		font-size: 0.85rem;
		margin: 1.5rem 0 2.5rem;
		padding: 0.75rem 1rem;
		background: var(--panel);
		border-radius: 0.3rem;
	}
	.facts dt {
		font-size: 0.65rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted);
		font-weight: 600;
		padding-top: 0.2rem;
	}
	.facts dd {
		margin: 0;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	@media (max-width: 640px) {
		/* an overview's table of commands scrolls inside itself, not the page */
		.overview :global(table) {
			display: block;
			overflow-x: auto;
		}
		.facts {
			grid-template-columns: 1fr;
		}
		.facts dd {
			margin-bottom: 0.5rem;
		}
	}
</style>

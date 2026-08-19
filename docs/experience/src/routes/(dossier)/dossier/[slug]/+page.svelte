<script lang="ts">
	import Commentable from '$lib/components/Commentable.svelte';
	import { STATUS_STYLE } from '$lib/dossier/types';

	let { data } = $props();
	const st = $derived(STATUS_STYLE[data.concept.status]);
</script>

<svelte:head>
	<title>{data.concept.term} · gnx dossier</title>
</svelte:head>

<article class="doc folio">
	<nav class="crumbs">
		<a href="/dossier">Summit</a>
		<span class="sep">/</span>
		{#if data.group}
			<a href="/dossier?region={data.group.id}">{data.group.title}</a>
			<span class="sep">/</span>
		{/if}
		<span class="here">{data.concept.term}</span>
	</nav>

	<header class="folio-head">
		<h1>{data.concept.term}</h1>
		<div class="tags">
			{#if data.concept.layer}<span class="layer">{data.concept.layer}</span>{/if}
			<span class="badge" style="color:{st.color};background:{st.bg}">{st.label}</span>
		</div>
	</header>

	<p class="folio-hint">
		Select any line — or use <span class="plus">+</span> in the margin — to comment. This is where we
		converge.
	</p>

	<Commentable doc={data.doc} blocks={data.blocks} threads={data.threads} />

	<nav class="pager">
		<a href="/dossier">← back to the map</a>
		{#if data.group}
			<a class="next" href="/dossier?region={data.group.id}">{data.group.title} →</a>
		{/if}
	</nav>
</article>

<style>
	.folio {
		max-width: 64rem;
		margin: 0 auto;
		padding: 2.5rem 2rem 7rem;
	}
	/* keep the head + breadcrumb + pager aligned to the reading column, not the full folio width */
	.crumbs,
	.folio-head,
	.pager {
		max-width: 44rem;
	}
	.crumbs {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font: 0.8rem var(--mono);
		color: var(--muted);
		border-bottom: 1px solid var(--line);
		padding-bottom: 0.8rem;
		margin-bottom: 1.8rem;
		flex-wrap: wrap;
	}
	.crumbs a {
		color: var(--muted);
		text-decoration: none;
	}
	.crumbs a:hover {
		color: var(--accent);
	}
	.crumbs .sep {
		color: var(--line);
	}
	.crumbs .here {
		color: var(--fg);
	}
	.folio-head {
		margin-bottom: 1.6rem;
	}
	.folio-head h1 {
		font-family: var(--sans);
		font-size: 2.1rem;
		line-height: 1.15;
		letter-spacing: -0.02em;
		margin: 0 0 0.6rem;
	}
	.folio-head .tags {
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}
	.folio-head .layer {
		font: 0.72rem var(--mono);
		color: var(--muted);
	}
	.folio-head .badge {
		font: 600 0.66rem var(--mono);
		letter-spacing: 0.05em;
		text-transform: uppercase;
		padding: 0.12rem 0.5rem;
		border-radius: 0.3rem;
	}
	.folio-hint {
		font: 0.8rem var(--mono);
		color: var(--muted);
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 0.55rem 0.8rem;
		margin: 0 0 1.8rem;
		background: color-mix(in oklab, var(--accent) 5%, transparent);
	}
	.folio-hint .plus {
		display: inline-block;
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 0 0.35rem;
		color: var(--accent);
	}
	.pager {
		margin-top: 3rem;
	}
</style>

<script lang="ts">
	import Commentable from '$lib/components/Commentable.svelte';

	let { data } = $props();

	const reading = $derived(data.docs);
	const idx = $derived(reading.findIndex((d: { slug: string }) => d.slug === data.doc.slug));
	const prev = $derived(idx > 0 ? reading[idx - 1] : null);
	const next = $derived(idx >= 0 && idx < reading.length - 1 ? reading[idx + 1] : null);
</script>

<svelte:head>
	<title>{data.doc.title} · gnx genesis</title>
</svelte:head>

<article class="doc">
	{#if data.doc.status || data.doc.mode}
		<div class="doc-meta">
			{#if data.doc.status}<span class="status status-{data.doc.status}">{data.doc.status}</span>{/if}
			{#if data.doc.mode}<span class="mode">{data.doc.mode}</span>{/if}
			<span class="sec">{data.doc.section}</span>
		</div>
	{/if}

	<Commentable doc={data.doc.slug} blocks={data.doc.blocks} threads={data.threads} />

	<nav class="pager">
		{#if prev}
			<a href="/docs/{prev.slug}">← {prev.title}</a>
		{:else}
			<a href="/">← Cover</a>
		{/if}
		{#if next}
			<a href="/docs/{next.slug}" class="next">{next.title} →</a>
		{/if}
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
	.doc-meta .status {
		padding: 0.1rem 0.4rem;
		border-radius: 0.2rem;
		font-weight: 700;
	}
	.status-shipped {
		background: color-mix(in oklab, green 22%, transparent);
		color: color-mix(in oklab, green 65%, currentColor);
	}
	.status-planned {
		background: color-mix(in oklab, #c98a00 24%, transparent);
		color: color-mix(in oklab, #c98a00 72%, currentColor);
	}
	.status-proposed {
		background: color-mix(in oklab, currentColor 12%, transparent);
		opacity: 0.7;
	}
	.doc-meta .mode,
	.doc-meta .sec {
		opacity: 0.5;
		font-weight: 600;
	}
	.doc-meta .mode::before {
		content: '·';
		margin-right: 0.5rem;
		opacity: 0.5;
	}
</style>

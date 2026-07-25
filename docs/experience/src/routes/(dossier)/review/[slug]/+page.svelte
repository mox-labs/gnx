<script lang="ts">
	import Annotatable from '$lib/components/Annotatable.svelte';

	let { data } = $props();

	const counts = $derived(data.counts as { open: number; answered: number; spawned: number });
</script>

<svelte:head><title>{data.meta.title} · gnx review</title></svelte:head>

<article class="doc folio">
	<nav class="crumbs">
		<a href="/review">Review</a>
		<span class="sep">/</span>
		<span class="here">{data.meta.title}</span>
	</nav>

	<header class="folio-head">
		<div class="bar">
			<span class="chip {data.meta.status}">{data.meta.status}</span>
			<span class="q open">{counts.open} open</span>
			<span class="q answered">{counts.answered} answered</span>
			<span class="q spawned">{counts.spawned} spawned</span>
			<span class="opened">opened {data.meta.opened}</span>
		</div>
	</header>

	{#if data.error}
		<div class="errbox">
			<b>Source artifact unavailable.</b>
			{data.error}
		</div>
	{:else}
		<p class="folio-hint">
			Select any line — or use <span class="plus">+</span> beside a block — to comment or raise a
			question in the margin. Reply to a question to answer it, or spawn it into a mission.
		</p>

		<Annotatable doc={data.doc} blocks={data.blocks} threads={data.threads} reviewMode />
	{/if}

	<nav class="pager">
		<a href="/review">← back to reviews</a>
	</nav>
</article>

<style>
	.folio {
		max-width: 64rem;
		margin: 0 auto;
		padding: 2.5rem 2rem 7rem;
	}
	.crumbs,
	.folio-head,
	.folio-hint,
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
		margin-bottom: 1.4rem;
	}
	.bar {
		display: flex;
		align-items: center;
		gap: 0.9rem;
		flex-wrap: wrap;
		font: 0.76rem var(--mono);
		color: var(--muted);
	}
	.chip {
		font: 600 0.64rem var(--mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
		padding: 0.12rem 0.5rem;
		border-radius: 0.3rem;
		border: 1px solid var(--line);
	}
	.chip.flowing {
		color: var(--constraint);
		border-color: var(--constraint);
	}
	.chip.settled {
		color: var(--muted);
	}
	.q.open {
		color: var(--constraint);
	}
	.q.answered {
		color: var(--resolved);
	}
	.q.spawned {
		color: var(--accent);
	}
	.bar .opened {
		margin-left: auto;
	}
	.errbox {
		border: 1px solid var(--constraint);
		color: var(--constraint);
		border-radius: 7px;
		padding: 0.8rem 1rem;
		font: 0.82rem var(--mono);
		margin: 1.5rem 0;
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
		font: 0.85rem var(--mono);
	}
	.pager a {
		text-decoration: none;
		color: var(--accent);
	}
</style>

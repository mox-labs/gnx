<script lang="ts">
	let { data } = $props();

	type ReviewRow = {
		slug: string;
		title: string;
		status: 'flowing' | 'settled';
		opened: string;
		counts: { open: number; answered: number; spawned: number };
	};
	const reviews = $derived(data.reviews as ReviewRow[]);
</script>

<svelte:head><title>Review · gnx dossier</title></svelte:head>

<div class="doc">
	<h1>Review</h1>

	<p>
		The inquiry twin of the dossier. A dossier folio equips a ruling; a review equips inquiry — a
		research artifact rendered as annotatable blocks, carrying an open-questions register. Questions
		flow until each is answered by research, spawned as a separate mission, or dissolved; then the
		review is settled.
	</p>

	{#if reviews.length === 0}
		<p class="muted">No reviews open. Add one to <code>docs/reviews.json</code>.</p>
	{:else}
		<ul class="reviews">
			{#each reviews as r (r.slug)}
				<li>
					<a href="/review/{r.slug}">
						<div class="head">
							<span class="t">{r.title}</span>
							<span class="chip {r.status}">{r.status}</span>
						</div>
						<div class="counts">
							<span class="q open">{r.counts.open} open</span>
							<span class="q answered">{r.counts.answered} answered</span>
							<span class="q spawned">{r.counts.spawned} spawned</span>
							<span class="opened">opened {r.opened}</span>
						</div>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.reviews {
		list-style: none;
		padding: 0;
		margin: 1.4rem 0 0;
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
	}
	.reviews a {
		display: block;
		text-decoration: none;
		color: inherit;
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 0.8rem 1rem;
	}
	.reviews a:hover {
		border-color: var(--accent);
	}
	.head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.8rem;
	}
	.head .t {
		font: 1rem var(--sans);
		font-weight: 600;
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
	.counts {
		display: flex;
		flex-wrap: wrap;
		gap: 0.9rem;
		margin-top: 0.5rem;
		font: 0.76rem var(--mono);
		color: var(--muted);
	}
	.q.open {
		color: var(--constraint);
	}
	.q.answered {
		color: var(--resolved);
	}
	.opened {
		margin-left: auto;
	}
</style>

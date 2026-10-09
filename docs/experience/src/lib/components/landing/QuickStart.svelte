<script lang="ts">
	// South panel — the one runnable path, stated without dates or version claims so it
	// cannot go stale. What exists is the catalog, which is read from source.
	import { base } from '$app/paths';
</script>

<div class="quickstart">
	<span class="badge">install</span>
	<p>
		<code>/plugin marketplace add mox-labs/gnx</code> — then pick from
		<a href="{base}/catalog">the catalog</a>.
	</p>
</div>

<style>
	.quickstart {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		font-family: var(--font-mono);
		font-size: var(--type-sm);
		color: var(--muted);
		max-width: 40rem;
		justify-content: center;
		flex-wrap: wrap;
	}
	.badge {
		font-size: var(--type-xs);
		letter-spacing: 0.06em;
		text-transform: uppercase;
		font-weight: 600;
		padding: 0.05rem 0.4rem;
		border-radius: 0.2rem;
		background: var(--ev-strong-bg);
		color: var(--ev-strong);
		white-space: nowrap;
	}
	.quickstart p {
		margin: 0;
	}
	.quickstart a {
		color: var(--accent);
		text-underline-offset: 3px;
	}
</style>

<script lang="ts">
	// South panel — the honest "what runs today" line, so the immersive surface
	// never overclaims. The plugins ship; the gnx CLI is designed, not built.
	import { base } from '$app/paths';
</script>

<div class="quickstart">
	<span class="badge">shipped today</span>
	<p>
		The plugin components install into Claude Code now. The gnx CLI is designed, not yet built —
		<a href="{base}/docs/status">what's real vs planned</a>.
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

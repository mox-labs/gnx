<script lang="ts">
	// East panel — the ways in. The public front doors lead (human + agent);
	// the dossier is the one discreet seam to the internal register — local only (D22).
	import { base } from '$app/paths';
	import { page } from '$app/state';

	const links = $derived([
		{ href: `${base}/docs/what-is-gnx`, label: 'What is gnx', primary: true },
		{ href: `${base}/catalog`, label: 'Browse the catalog' },
		{ href: `${base}/docs/install-a-plugin`, label: 'Install a plugin' },
		{ href: `${base}/llms.txt`, label: 'For agents — llms.txt' },
		...(page.data.publicOnly ? [] : [{ href: '/dossier', label: 'the dossier · internal', muted: true }])
	]);
</script>

<nav class="cta">
	{#each links as l (l.href)}
		<a href={l.href} class:primary={l.primary} class:muted={l.muted}>
			{l.label}<span class="arrow">→</span>
		</a>
	{/each}
</nav>

<style>
	.cta {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		font-family: var(--font-mono);
		text-align: right;
	}
	.cta a {
		text-decoration: none;
		color: var(--muted);
		font-size: var(--type-sm);
		display: inline-flex;
		align-items: baseline;
		justify-content: flex-end;
		gap: 0.5ch;
		transition: color 0.12s;
	}
	.cta a .arrow {
		opacity: 0;
		transform: translateX(-3px);
		transition:
			opacity 0.12s,
			transform 0.12s;
	}
	.cta a:hover {
		color: var(--accent);
	}
	.cta a:hover .arrow {
		opacity: 1;
		transform: translateX(0);
	}
	.cta a.primary {
		color: var(--ci-green);
		font-size: var(--type-base);
	}
	.cta a.primary .arrow {
		opacity: 1;
		transform: none;
	}
	.cta a.primary:hover {
		color: var(--resolved);
	}
	.cta a.muted {
		font-size: var(--type-xs, 0.72rem);
		opacity: 0.55;
		margin-top: var(--space-2);
	}
	@media (max-width: 768px) {
		.cta {
			text-align: center;
			align-items: center;
		}
		.cta a {
			justify-content: center;
		}
	}
</style>

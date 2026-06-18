<script lang="ts">
	// East panel — the ways in. The dossier is the primary CTA; the rest are
	// the public front doors (human + agent).
	const links = [
		{ href: '/dossier', label: 'Enter the dossier', primary: true },
		{ href: '/docs/what-is-gnx', label: 'What is gnx' },
		{ href: '/docs/install-a-plugin', label: 'Install a plugin' },
		{ href: '/llms.txt', label: 'For agents — llms.txt' }
	];
</script>

<nav class="cta">
	{#each links as l (l.href)}
		<a href={l.href} class:primary={l.primary}>
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

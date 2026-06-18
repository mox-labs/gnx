<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';
	import { page } from '$app/state';

	let { data, children } = $props();

	type Doc = {
		slug: string;
		title: string;
		section: string;
		register: string;
		order: number;
		status?: string;
	};

	const isActive = (slug: string) => page.url.pathname === `/docs/${slug}`;
	// The landing ("/") is immersive — full-bleed, no sidebar chrome.
	const isImmersive = $derived(page.url.pathname === '/');

	const SECTIONS = ['start', 'guides', 'reference', 'explanation', 'design', 'build'] as const;
	const LABEL: Record<string, string> = {
		start: 'Start',
		guides: 'Guides',
		reference: 'Reference',
		explanation: 'Explanation',
		design: 'Design',
		build: 'The Build'
	};

	// Group by section, drop empties (no empty Diataxis buckets), split by register.
	const groups = $derived(
		SECTIONS.map((s) => ({
			section: s,
			label: LABEL[s],
			docs: (data.docs as Doc[]).filter((d) => d.section === s)
		})).filter((g) => g.docs.length)
	);
	const publicGroups = $derived(groups.filter((g) => g.docs[0].register === 'public'));
	const internalGroups = $derived(groups.filter((g) => g.docs[0].register === 'internal'));
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>gnx · genesis dossier</title>
</svelte:head>

{#if isImmersive}
	{@render children()}
{:else}
<div class="shell">
	<aside class="nav">
		<div class="brand">
			<a href="/dossier" class:active={page.url.pathname === '/dossier'}><b>gnx</b> · genesis dossier</a>
		</div>

		<details class="nav-body">
			<summary>Browse the dossier</summary>
			<div class="nav-links">

		{#if publicGroups.length}
			<div class="register-label">Public</div>
			{#each publicGroups as g (g.section)}
				<div class="section-label">{g.label}</div>
				<ol>
					{#each g.docs as doc (doc.slug)}
						<li>
							<a href="/docs/{doc.slug}" class:active={isActive(doc.slug)}>
								<span>{doc.title}</span>
								{#if doc.status}<span class="status status-{doc.status}">{doc.status}</span>{/if}
								{#if data.counts.perDoc[doc.slug]}
									<span class="count">{data.counts.perDoc[doc.slug]}</span>
								{/if}
							</a>
						</li>
					{/each}
				</ol>
			{/each}
		{/if}

		<div class="register-label">Internal</div>
		{#each internalGroups as g (g.section)}
			<div class="section-label">{g.label}</div>
			<ol>
				{#each g.docs as doc (doc.slug)}
					<li>
						<a href="/docs/{doc.slug}" class:active={isActive(doc.slug)}>
							{#if g.section === 'design' && doc.order >= 0 && doc.order < 90}
								<span class="num">{String(doc.order).padStart(2, '0')}</span>
							{/if}
							<span>{doc.title}</span>
							{#if doc.status}<span class="status status-{doc.status}">{doc.status}</span>{/if}
							{#if data.counts.perDoc[doc.slug]}
								<span class="count">{data.counts.perDoc[doc.slug]}</span>
							{/if}
						</a>
					</li>
				{/each}
				{#if g.section === 'build'}
					<li>
						<a href="/evaluation" class:active={page.url.pathname === '/evaluation'}>
							<span>Evaluation</span>
						</a>
					</li>
				{/if}
			</ol>
		{/each}

		<div class="inbox">
			<a
				href="/feedback"
				class:active={page.url.pathname === '/feedback'}
				style="display:flex;gap:.5rem;align-items:baseline;text-decoration:none;font-size:.85rem"
			>
				<span>feedback inbox</span>
				{#if data.counts.total}<span class="count">{data.counts.total}</span>{/if}
			</a>
		</div>

			</div>
		</details>
	</aside>
	<main>
		{@render children()}
	</main>
</div>
{/if}

<style>
	.register-label {
		font-size: 0.68rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		font-weight: 700;
		opacity: 0.5;
		margin: 1.6rem 0 0.3rem;
	}
	.register-label:first-of-type {
		margin-top: 0.6rem;
	}
	.status {
		font-size: 0.6rem;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		padding: 0.04rem 0.32rem;
		border-radius: 0.2rem;
		margin-left: auto;
		font-weight: 600;
		white-space: nowrap;
	}
	/* maturity badges wired to the brand's evidence-brightness scale:
	   shipped ≈ strong, planned ≈ moderate, proposed ≈ speculative */
	.status-shipped {
		background: var(--ev-strong-bg);
		color: var(--ev-strong);
	}
	.status-planned {
		background: var(--ev-moderate-bg);
		color: var(--ev-moderate);
	}
	.status-proposed {
		background: var(--ev-speculative-bg);
		color: var(--ev-speculative);
	}
</style>

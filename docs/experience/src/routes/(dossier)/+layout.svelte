<script lang="ts">
	import { page } from '$app/state';
	import { GROUPS } from '$lib/registers';

	let { data, children } = $props();

	type Doc = {
		slug: string;
		title: string;
		section: string;
		register: string;
		order: number;
		status?: string;
	};
	type NavConcept = { slug: string; term: string; status: string; open: number };
	type NavRegion = { id: string; title: string; concepts: NavConcept[] };

	// the active concept folio (/dossier/<slug>) and the region that holds it — open that region.
	const currentSlug = $derived((page.url.pathname.match(/^\/dossier\/([^/]+)$/) ?? [])[1] ?? null);
	const dossier = $derived(data.dossier as NavRegion[]);
	const activeRegion = $derived(
		currentSlug ? (dossier.find((r) => r.concepts.some((c) => c.slug === currentSlug))?.id ?? null) : null
	);

	// The private wing's doc families — driven by the register config, one nav block per
	// group. Adding a register = a config entry + a content/<section>/ dir, no new markup.
	const navGroups = $derived(
		GROUPS.map((g) => ({
			...g,
			docs: (data.docs as Doc[]).filter((d) => g.sections.includes(d.section))
		})).filter((g) => g.docs.length)
	);
</script>

<svelte:head>
	<title>gnx · dossier</title>
</svelte:head>

<div class="shell">
	<aside class="nav">
		<div class="brand">
			<a href="/dossier" class:active={page.url.pathname === '/dossier'}><b>gnx</b> · dossier</a>
		</div>

		<details class="nav-body">
			<summary>Navigate</summary>
			<div class="nav-links">
				<!-- the single exit to the public wing -->
				<div class="seam">
					<a href="/docs/overview">← Documentation</a>
				</div>

				<!-- THE DOSSIER — the spine: the map, then each region → its concepts -->
				<a class="map" href="/dossier" class:active={page.url.pathname === '/dossier'}>The map</a>
				<!-- All regions open by default: the tree fits on screen, so disclosure was
				     pure click-tax (and the old reactive `open` re-closed regions on every
				     navigation). `open` is static — after first render the toggles are the
				     reader's, and the layout persists across client-side navs. -->
				{#each dossier as region (region.id)}
					<details class="region" open>
						<summary class:here={region.id === activeRegion}>{region.title}</summary>
						<ol>
							{#each region.concepts as c (c.slug)}
								<li>
									<a href="/dossier/{c.slug}" class:active={currentSlug === c.slug}>
										<span class="dot {c.status}" aria-hidden="true">●</span>
										<span class="sr-only">{c.status}</span>
										<span class="t">{c.term}</span>
										{#if c.open}<span class="count">{c.open}</span>{/if}
									</a>
								</li>
							{/each}
						</ol>
					</details>
				{/each}

				<!-- THE DOC FAMILIES — Specs / Proposals / Appendix, one block per configured group -->
				{#each navGroups as g (g.id)}
					<div class="register-label">{g.label}</div>
					<ol>
						{#each g.docs as doc (doc.slug)}
							<li>
								<a
									href="/dossier/{g.id}/{doc.slug}"
									class:active={page.url.pathname === `/dossier/${g.id}/${doc.slug}`}
								>
									<span class="t">{doc.title}</span>
									{#if data.counts.perDoc[doc.slug]}
										<span class="count">{data.counts.perDoc[doc.slug]}</span>
									{/if}
								</a>
							</li>
						{/each}
						{#if g.id === 'appendix'}
							<li>
								<a href="/evaluation" class:active={page.url.pathname === '/evaluation'}>
									<span class="t">Evaluation</span>
								</a>
							</li>
						{/if}
					</ol>
				{/each}

				<div class="inbox">
					<a
						href="/review"
						class:active={page.url.pathname.startsWith('/review')}
						style="display:flex;gap:.5rem;align-items:baseline;text-decoration:none;font-size:.85rem"
					>
						<span>Review</span>
					</a>
				</div>

				<div class="inbox">
					<a
						href="/feedback"
						class:active={page.url.pathname === '/feedback'}
						style="display:flex;gap:.5rem;align-items:baseline;text-decoration:none;font-size:.85rem"
					>
						<span>Feedback inbox</span>
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

<style>
	.register-label {
		font-size: 0.68rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		font-weight: 700;
		opacity: 0.5;
		margin: 1.6rem 0 0.4rem;
	}
	a.map {
		display: block;
		font-size: 0.85rem;
		color: var(--muted);
		text-decoration: none;
		padding: 0.28rem 0.5rem;
		border-radius: 5px;
		margin-bottom: 0.2rem;
	}
	a.map:hover,
	a.map.active {
		background: var(--panel);
		color: var(--fg);
	}
	/* region disclosure — progressive: collapsed until you enter it */
	.region {
		margin: 0.1rem 0;
	}
	.region > summary {
		list-style: none;
		cursor: pointer;
		font-size: 0.85rem;
		color: var(--fg);
		padding: 0.28rem 0.5rem;
		border-radius: 5px;
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.region > summary::-webkit-details-marker {
		display: none;
	}
	.region > summary::before {
		content: '▸';
		color: var(--muted);
		font-size: 0.7rem;
		transition: transform 0.12s;
	}
	.region[open] > summary::before {
		transform: rotate(90deg);
	}
	.region > summary:hover {
		background: var(--panel);
	}
	.region > summary.here {
		color: var(--accent, var(--fg));
	}
	.region ol {
		margin: 0.1rem 0 0.3rem;
		padding-left: 0.9rem;
	}
	.region .dot {
		font-size: 0.5rem;
		line-height: 1;
	}
	.dot.settled {
		color: var(--muted);
	}
	.dot.converged {
		color: var(--resolved);
	}
	.dot.contested {
		color: var(--accent);
	}
	.dot.open {
		color: var(--constraint);
	}
	.t {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.seam {
		margin-bottom: 0.8rem;
	}
	.seam a {
		font-size: 0.72rem;
		color: var(--muted);
		text-decoration: none;
		letter-spacing: 0.04em;
	}
	.seam a:hover {
		color: var(--fg);
	}
</style>

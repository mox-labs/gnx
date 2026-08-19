<script lang="ts">
	import { page } from '$app/state';
	import { base } from '$app/paths';

	let { data, children } = $props();

	type Doc = {
		slug: string;
		title: string;
		section: string;
		register: string;
		order: number;
		status?: string;
	};

	// Only the cinematic landing is full-bleed; the docs share the public shell.
	const isImmersive = $derived(page.url.pathname === `${base}/` || page.url.pathname === base);

	// The public wing is the preview of the public gnx docsite: pragmatics only,
	// Diataxis sections, no internal register in the spine.
	// orientation → action → understanding → verify (vyasa, ia-two-wings)
	const SECTIONS = ['start', 'guides', 'explanation', 'reference', 'gep'] as const;
	const LABEL: Record<string, string> = {
		start: 'Start',
		guides: 'Guides',
		explanation: 'Concepts',
		reference: 'Reference',
		gep: 'Proposals'
	};

	const groups = $derived(
		SECTIONS.map((s) => ({
			section: s,
			label: LABEL[s],
			docs: (data.docs as Doc[]).filter((d) => d.section === s && d.register === 'public')
		})).filter((g) => g.docs.length)
	);

	const isDoc = (slug: string) => page.url.pathname === `${base}/docs/${slug}`;
</script>

<svelte:head>
	<title>gnx</title>
</svelte:head>

{#if isImmersive}
	{@render children()}
{:else}
	<!-- Site-wide, not per-page: gnx is pre-generation-0, the catalog was deliberately cut
	     back to what has been evaluated, and the docs are being rebuilt around it. A reader
	     should know that before they read anything else, not after. -->
	<div class="uc" role="note">
		<b>Under construction.</b> gnx is pre-generation-0. The catalog is small on purpose —
		most components sit in <code>incubator/</code> until they are evaluated — and these docs
		are being rebuilt alongside it.
	</div>

	<div class="shell">
		<aside class="nav">
			<div class="brand">
				<a href="{base}/"><b>gnx</b></a>
			</div>

			<details class="nav-body">
				<summary>Navigate</summary>
				<div class="nav-links">
					<!-- the catalog is the demonstration — it leads -->
					<div class="section-label">Catalog</div>
					<ol>
						<li>
							<a href="{base}/catalog" class:active={page.url.pathname === `${base}/catalog`}>
								<span class="t">Browse the catalog</span>
							</a>
						</li>
					</ol>

					{#each groups as g (g.section)}
						<div class="section-label">{g.label}</div>
						<ol>
							{#each g.docs as doc (doc.slug)}
								<li>
									<a href="{base}/docs/{doc.slug}" class:active={isDoc(doc.slug)}>
										<span class="t">{doc.title}</span>
										<!-- badge "shipped" only: most docs are planned, and badging every nav item
										     announces construction everywhere. The status page carries the full picture. -->
										{#if doc.status === 'shipped'}<span class="status status-shipped">shipped</span>{/if}
										{#if data.counts.perDoc[doc.slug]}
											<span class="count">{data.counts.perDoc[doc.slug]}</span>
										{/if}
									</a>
								</li>
							{/each}
						</ol>
					{/each}

					<!-- the one seam to the internal register: discreet, at the foot.
					     Labeled "Design" — an external audience won't know the term "dossier". -->
					{#if !data.publicOnly}
						<div class="seam">
							<a href="/dossier">Design →</a>
						</div>
					{/if}
				</div>
			</details>
		</aside>
		<main>
			{@render children()}
		</main>
	</div>
{/if}

<style>
	.t {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
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
	/* the one badge shown: shipped, wired to the brand's evidence-brightness scale */
	.status-shipped {
		background: var(--ev-strong-bg);
		color: var(--ev-strong);
	}
	.seam {
		margin-top: 2rem;
		padding-top: 0.8rem;
		border-top: 1px solid var(--line, rgba(128, 128, 128, 0.2));
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

	.uc {
		background: var(--panel);
		border-bottom: 1px solid var(--line, rgba(128, 128, 128, 0.25));
		border-left: 3px solid var(--constraint, #b8860b);
		color: var(--muted);
		font-size: 0.8rem;
		line-height: 1.5;
		padding: 0.6rem 1.1rem;
	}
	.uc b {
		color: var(--fg);
	}
	.uc code {
		font-size: 0.75rem;
	}
</style>

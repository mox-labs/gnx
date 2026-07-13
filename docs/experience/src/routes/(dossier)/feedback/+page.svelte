<script lang="ts">
	import { invalidateAll } from '$app/navigation';

	let { data } = $props();
	let showResolved = $state(false);

	async function setStatus(doc: string, thread: string, kind: 'resolve' | 'reopen') {
		await fetch('/api/feedback', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ doc, thread, kind, author: 'yash' })
		});
		await invalidateAll();
	}

	const docs = $derived(Object.entries(data.threads));

	// Feedback ledgers are keyed by their reading surface: `dossier-<slug>` for a concept folio,
	// otherwise a markdown doc slug. Map the key back to the route it actually lives at.
	const internalSection = $derived(
		new Map(
			(data.docs as { slug: string; register: string; section: string }[])
				.filter((d) => d.register !== 'public')
				.map((d) => [d.slug, d.section])
		)
	);
	const hrefFor = (doc: string) => {
		if (doc === 'dossier-summit') return '/dossier';
		if (doc.startsWith('dossier-')) return `/dossier/${doc.slice('dossier-'.length)}`;
		const section = internalSection.get(doc);
		if (!section) return `/docs/${doc}`;
		return section === 'spec' ? `/dossier/spec/${doc}` : `/dossier/appendix/${doc}`;
	};
	const labelFor = (doc: string) => {
		if (doc === 'dossier-summit') return 'the map · dossier';
		return doc.startsWith('dossier-') ? `${doc.slice('dossier-'.length)} · dossier` : doc;
	};
</script>

<svelte:head>
	<title>Feedback · gnx dossier</title>
</svelte:head>

<div class="doc">
	<h1>Feedback inbox</h1>
	<p class="muted">
		<label>
			<input type="checkbox" bind:checked={showResolved} />
			show resolved
		</label>
	</p>

	{#if docs.length === 0}
		<p class="muted">No feedback yet. Open a doc and comment on any block.</p>
	{/if}

	{#each docs as [doc, threads] (doc)}
		{@const visible = threads.filter((t) => showResolved || t.status === 'open')}
		{#if visible.length}
			<section class="inbox-doc">
				<h2><a href={hrefFor(doc)}>{labelFor(doc)}</a></h2>
				{#each visible as t (t.id)}
					<div class="panel" style="border-left-color: {t.status === 'resolved' ? 'var(--resolved)' : 'var(--accent)'}">
						<div class="meta">
							<span class="status {t.status}">{t.status}</span>
							<span>{new Date(t.ts).toLocaleString()}</span>
							<a href="{hrefFor(doc)}#{t.bid}">→ block</a>
						</div>
						<div class="quote">{t.quote}</div>
						{#each t.items as item, i (i)}
							<div class="item">
								<span class="who">{item.author}{item.kind === 'resolve' ? ' · resolved' : ''}{item.kind === 'reopen' ? ' · reopened' : ''}</span>{item.body}
							</div>
						{/each}
						<div class="row">
							{#if t.status === 'open'}
								<button class="linkish" onclick={() => setStatus(doc, t.id, 'resolve')}>resolve</button>
							{:else}
								<button class="linkish" onclick={() => setStatus(doc, t.id, 'reopen')}>reopen</button>
							{/if}
						</div>
					</div>
				{/each}
			</section>
		{/if}
	{/each}
</div>

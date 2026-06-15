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
</script>

<svelte:head>
	<title>feedback · gnx design</title>
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
				<h2><a href="/docs/{doc}">{doc}</a></h2>
				{#each visible as t (t.id)}
					<div class="panel" style="border-left-color: {t.status === 'resolved' ? 'var(--resolved)' : 'var(--accent)'}">
						<div class="meta">
							<span class="status {t.status}">{t.status}</span>
							<span>{new Date(t.ts).toLocaleString()}</span>
							<a href="/docs/{doc}#{t.bid}">→ block</a>
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

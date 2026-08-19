<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { browser } from '$app/environment';
	import { clearAnnots, paintQuote } from '$lib/client/anchor';
	import type { DocBlock } from '$lib/server/content';
	import type { Thread } from '$lib/server/feedback';

	interface PendingSelection {
		bid: string;
		quote: string;
		prefix: string;
		suffix: string;
	}

	let {
		doc,
		block,
		threads = [],
		pending = null,
		reviewMode = false,
		onConsumed
	}: {
		doc: string;
		block: DocBlock;
		threads?: Thread[];
		pending?: PendingSelection | null;
		/** review surface: root marks can be questions, and question replies can spawn a mission */
		reviewMode?: boolean;
		onConsumed?: () => void;
	} = $props();

	let contentEl: HTMLElement | undefined = $state();
	let open = $state(false);
	let composing = $state(false);
	let body = $state('');
	let replyBodies = $state<Record<string, string>>({});
	let author = $state((browser && localStorage.getItem('gnx-fb-author')) || 'yash');
	let busy = $state(false);
	let focused = $state<string | null>(null);
	// review-mode root-mark kind; ignored (stays 'comment') on non-review surfaces.
	let rootKind = $state<'comment' | 'question'>('comment');

	const mine = $derived(pending && pending.bid === block.id ? pending : null);
	const openCount = $derived(threads.filter((t) => t.status === 'open').length);

	$effect(() => {
		if (mine) {
			open = true;
			composing = true;
		}
	});

	// Paint selection-anchored threads as highlights whenever threads change.
	$effect(() => {
		const el = contentEl;
		if (!el) return;
		void threads;
		clearAnnots(el);
		for (const t of threads) {
			if (!t.anchored || !t.quote) continue;
			paintQuote(el, {
				quote: t.quote,
				prefix: t.prefix,
				suffix: t.suffix,
				threadId: t.id,
				resolved: t.status === 'resolved',
				onClick: (id) => {
					open = true;
					focused = id;
				}
			});
		}
	});

	async function send(payload: Record<string, string | undefined>) {
		busy = true;
		try {
			if (browser) localStorage.setItem('gnx-fb-author', author);
			const res = await fetch('/api/feedback', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ doc, author, ...payload })
			});
			if (!res.ok) throw new Error(await res.text());
			await invalidateAll();
		} finally {
			busy = false;
		}
	}

	async function comment() {
		if (!body.trim()) return;
		const anchor = mine
			? { quote: mine.quote, prefix: mine.prefix, suffix: mine.suffix }
			: { quote: block.excerpt };
		const kind = reviewMode ? rootKind : 'comment';
		await send({ kind, bid: block.id, body: body.trim(), ...anchor });
		body = '';
		composing = false;
		onConsumed?.();
	}

	async function reply(thread: string) {
		const text = (replyBodies[thread] ?? '').trim();
		if (!text) return;
		await send({ kind: 'reply', thread, body: text });
		replyBodies[thread] = '';
	}

	// review surface: cut a question into a separate mission — the body carries the mission slug.
	async function spawn(thread: string) {
		const text = (replyBodies[thread] ?? '').trim();
		if (!text) return;
		await send({ kind: 'spawn', thread, body: text });
		replyBodies[thread] = '';
	}

	function cancel() {
		composing = false;
		body = '';
		if (!threads.length) open = false;
		onConsumed?.();
	}
</script>

<section class="block {block.variant ?? ''}" id={block.id}>
	<div class="content" bind:this={contentEl}>
		<!-- eslint-disable-next-line svelte/no-at-html-tags -- trusted local markdown -->
		{@html block.html}
	</div>
	<div class="gutter">
		{#if threads.length}
			<button
				class="badge"
				class:resolved={openCount === 0}
				title="{threads.length} thread(s), {openCount} open"
				onclick={() => (open = !open)}
			>
				{threads.length}
			</button>
		{/if}
		<button
			class="add"
			title="Comment on this block (or select text to anchor a comment)"
			onclick={() => {
				open = true;
				composing = true;
			}}
		>
			+
		</button>
	</div>

	{#if open}
		<div class="panel">
			{#each threads as t (t.id)}
				<div class="thread" class:focused={focused === t.id}>
					<div class="meta">
						<span class="status {t.status}">{t.status}</span>
						<span>{new Date(t.ts).toLocaleString()}</span>
					</div>
					{#if t.anchored}
						<div class="quote">{t.quote}</div>
					{/if}
					{#each t.items as item, i (i)}
						<div class="item">
							<span class="who"
								>{item.author}{item.kind === 'resolve' ? ' · resolved' : ''}{item.kind === 'reopen'
									? ' · reopened'
									: ''}</span
							>{item.body}
						</div>
					{/each}
					<div class="row">
						<textarea
							placeholder="reply…"
							bind:value={replyBodies[t.id]}
							style="min-height:2.4rem"
						></textarea>
					</div>
					<div class="row">
						<button disabled={busy} onclick={() => reply(t.id)}>reply</button>
						{#if reviewMode && t.items[0]?.kind === 'question'}
							<button
								class="spawn"
								disabled={busy}
								title="cut this question into a separate mission (reply body = mission slug)"
								onclick={() => spawn(t.id)}
							>
								spawn
							</button>
						{/if}
						{#if t.status === 'open'}
							<button disabled={busy} onclick={() => send({ kind: 'resolve', thread: t.id })}>
								resolve
							</button>
						{:else}
							<button disabled={busy} onclick={() => send({ kind: 'reopen', thread: t.id })}>
								reopen
							</button>
						{/if}
					</div>
				</div>
			{/each}

			{#if composing || threads.length === 0}
				<div class="thread">
					{#if reviewMode}
						<div class="kindpick">
							<button class="linkish" class:on={rootKind === 'comment'} onclick={() => (rootKind = 'comment')}>
								comment
							</button>
							<button class="linkish" class:on={rootKind === 'question'} onclick={() => (rootKind = 'question')}>
								question
							</button>
						</div>
					{/if}
					{#if mine}
						<div class="quote">{mine.quote}</div>
					{/if}
					<textarea
						placeholder={mine ? 'comment on the selected text…' : 'comment on this block…'}
						bind:value={body}
					></textarea>
					<div class="row">
						<input type="text" bind:value={author} title="author" />
						<button class="primary" disabled={busy || !body.trim()} onclick={comment}>
							{reviewMode ? rootKind : 'comment'}
						</button>
						<button disabled={busy} onclick={cancel}>cancel</button>
					</div>
				</div>
			{:else}
				<div class="row">
					<button class="linkish" onclick={() => (composing = true)}>+ new thread</button>
				</div>
			{/if}
		</div>
	{/if}
</section>

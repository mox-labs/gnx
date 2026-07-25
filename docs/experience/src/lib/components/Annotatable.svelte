<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { browser } from '$app/environment';
	import { clearAnnots, paintQuote, offsetsOfRange, quoteAt } from '$lib/client/anchor';
	import type { DocBlock } from '$lib/server/content';
	import type { Thread } from '$lib/server/feedback';

	// An annotatable document: the whole artifact rendered as blocks in a content column, with a
	// right margin where every thread lives as a card anchored at its highlight (or its block top).
	// Marginalia, not inline panels — so a comment sits beside the exact line it is about, and a
	// giant section no longer stacks all its threads into one far-away box.
	let {
		doc,
		blocks,
		threads = [],
		reviewMode = false
	}: { doc: string; blocks: DocBlock[]; threads?: Thread[]; reviewMode?: boolean } = $props();

	interface Pending {
		bid: string;
		quote: string;
		prefix?: string;
		suffix?: string;
		excerpt?: string;
		/** vertical offset (px) of the anchor within the content column, at capture time */
		top: number;
	}

	// Element registries (plain — read imperatively during paint/layout, not rendered).
	const contentEls: Record<string, HTMLElement> = {};
	const cardEls: Record<string, HTMLElement> = {};
	let colEl: HTMLElement | undefined = $state();

	let wide = $state(true);
	let positions = $state<Record<string, number>>({});
	let focused = $state<string | null>(null);

	// Composer + selection state.
	let selMenu = $state<{ x: number; y: number; pending: Pending } | null>(null);
	let pending = $state<Pending | null>(null);
	let composeKind = $state<'comment' | 'question'>('comment');

	// Reply/compose form state.
	let body = $state('');
	let replyBodies = $state<Record<string, string>>({});
	let author = $state((browser && localStorage.getItem('gnx-fb-author')) || 'yash');
	let busy = $state(false);

	const COMPOSE_ID = '__compose';
	const GAP = 14; // px between stacked margin cards

	const threadsByBlock = $derived.by(() => {
		const map = new Map<string, Thread[]>();
		for (const t of threads) {
			const list = map.get(t.bid) ?? [];
			list.push(t);
			map.set(t.bid, list);
		}
		return map;
	});

	function rootKind(t: Thread): 'comment' | 'question' {
		return t.items[0]?.kind === 'question' ? 'question' : 'comment';
	}
	// A thread's disposition, mirrored from review.ts (kept inline so this client component
	// never imports the node:fs-bound server module). Questions read open/answered/spawned;
	// comments read open/resolved.
	function stateLabel(t: Thread): string {
		if (rootKind(t) === 'question') {
			if (t.items.some((i) => i.kind === 'spawn')) return 'spawned';
			return t.status === 'resolved' ? 'answered' : 'open';
		}
		return t.status;
	}
	function truncate(s: string, n = 140): string {
		return s.length > n ? s.slice(0, n).trimEnd() + '…' : s;
	}

	// ---- painting + layout ----------------------------------------------------

	function anchorEl(t: Thread): HTMLElement | null {
		if (!colEl) return null;
		if (t.anchored && t.quote) {
			const m = colEl.querySelector<HTMLElement>(`mark.annot[data-thread="${CSS.escape(t.id)}"]`);
			if (m) return m;
		}
		return document.getElementById(t.bid);
	}

	function relayout() {
		const col = colEl;
		if (!col || !wide) return;
		const colTop = col.getBoundingClientRect().top;
		const entries: { id: string; top: number; h: number }[] = [];
		for (const t of threads) {
			const el = cardEls[t.id];
			if (!el) continue;
			const a = anchorEl(t);
			const top = a ? a.getBoundingClientRect().top - colTop : 0;
			entries.push({ id: t.id, top, h: el.offsetHeight });
		}
		if (pending) {
			const el = cardEls[COMPOSE_ID];
			if (el) entries.push({ id: COMPOSE_ID, top: pending.top, h: el.offsetHeight });
		}
		// Sidenote collision: sort by desired anchor top, push each card below the previous.
		entries.sort((a, b) => a.top - b.top);
		let prevBottom = -Infinity;
		const next: Record<string, number> = {};
		for (const e of entries) {
			const y = Math.max(e.top, prevBottom + GAP);
			next[e.id] = y;
			prevBottom = y + e.h;
		}
		positions = next;
	}

	// Repaint highlights whenever threads change, then re-measure the margin (highlights are
	// wrapped into the DOM synchronously here; card heights settle on the next frame).
	$effect(() => {
		void threads;
		void blocks;
		const seen = new Set<HTMLElement>();
		for (const el of Object.values(contentEls)) {
			if (seen.has(el)) continue;
			seen.add(el);
			clearAnnots(el);
		}
		for (const t of threads) {
			if (!t.anchored || !t.quote) continue;
			const el = contentEls[t.bid];
			if (!el) continue;
			paintQuote(el, {
				quote: t.quote,
				prefix: t.prefix,
				suffix: t.suffix,
				threadId: t.id,
				resolved: t.status === 'resolved',
				onClick: focusCard
			});
		}
		requestAnimationFrame(relayout);
	});

	// Track the breakpoint: absolute margin above ~900px, inline-after-block below it.
	$effect(() => {
		const mq = window.matchMedia('(min-width: 900px)');
		const update = () => {
			wide = mq.matches;
			requestAnimationFrame(relayout);
		};
		update();
		mq.addEventListener('change', update);
		return () => mq.removeEventListener('change', update);
	});

	// Content reflows (font load, wrapping) shift anchor tops — re-run the collision pass.
	$effect(() => {
		if (!colEl) return;
		const ro = new ResizeObserver(() => requestAnimationFrame(relayout));
		ro.observe(colEl);
		return () => ro.disconnect();
	});

	function focusCard(id: string) {
		focused = id;
		requestAnimationFrame(() =>
			cardEls[id]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
		);
	}
	function scrollToAnchor(t: Thread) {
		anchorEl(t)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}

	// ---- new annotation: select text → floating affordance → composer card ----

	function captureSelection() {
		const sel = window.getSelection();
		selMenu = null;
		if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
		const range = sel.getRangeAt(0);
		const node =
			range.commonAncestorContainer instanceof Element
				? range.commonAncestorContainer
				: range.commonAncestorContainer.parentElement;
		const blockEl = node?.closest('.ablock');
		const contentEl = node?.closest('.content') as HTMLElement | null;
		if (!blockEl || !contentEl || !colEl?.contains(blockEl)) return;

		const offsets = offsetsOfRange(contentEl, range);
		if (!offsets) return;
		const { quote, prefix, suffix } = quoteAt(contentEl, offsets.start, offsets.end);
		if (!quote.trim() || quote.length > 500) return;

		const rect = range.getBoundingClientRect();
		selMenu = {
			x: rect.left + rect.width / 2,
			y: rect.top,
			pending: {
				bid: blockEl.id,
				quote,
				prefix,
				suffix,
				top: rect.top - (colEl.getBoundingClientRect().top ?? 0)
			}
		};
	}

	function startCompose() {
		if (!selMenu) return;
		pending = selMenu.pending;
		composeKind = 'comment';
		selMenu = null;
		window.getSelection()?.removeAllRanges();
		requestAnimationFrame(relayout);
	}

	function addToBlock(block: DocBlock) {
		const sec = document.getElementById(block.id);
		const top = sec && colEl ? sec.getBoundingClientRect().top - colEl.getBoundingClientRect().top : 0;
		pending = { bid: block.id, quote: '', excerpt: block.excerpt, top };
		composeKind = 'comment';
		requestAnimationFrame(relayout);
	}

	// ---- feedback API (lifted from Block.svelte; API + store unchanged) -------

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
		if (!body.trim() || !pending) return;
		const anchored = pending.prefix !== undefined || pending.suffix !== undefined;
		const anchor = anchored
			? { quote: pending.quote, prefix: pending.prefix, suffix: pending.suffix }
			: { quote: pending.excerpt ?? pending.quote };
		const kind = reviewMode ? composeKind : 'comment';
		await send({ kind, bid: pending.bid, body: body.trim(), ...anchor });
		body = '';
		pending = null;
	}

	async function reply(thread: string) {
		const text = (replyBodies[thread] ?? '').trim();
		if (!text) return;
		await send({ kind: 'reply', thread, body: text });
		replyBodies[thread] = '';
	}

	// review surface: cut a question into a separate mission — the reply body carries the slug.
	async function spawn(thread: string) {
		const text = (replyBodies[thread] ?? '').trim();
		if (!text) return;
		await send({ kind: 'spawn', thread, body: text });
		replyBodies[thread] = '';
	}

	function cancelCompose() {
		pending = null;
		body = '';
	}

	function contentRef(node: HTMLElement, id: string) {
		contentEls[id] = node;
		return {
			destroy() {
				if (contentEls[id] === node) delete contentEls[id];
			}
		};
	}
	function cardRef(node: HTMLElement, id: string) {
		cardEls[id] = node;
		requestAnimationFrame(relayout);
		return {
			destroy() {
				if (cardEls[id] === node) delete cardEls[id];
			}
		};
	}
</script>

<svelte:document
	onpointerup={captureSelection}
	onpointerdown={(e) => {
		if (selMenu && !(e.target as Element).closest('.selmenu')) selMenu = null;
	}}
/>

{#if selMenu}
	<button class="selmenu" style="left: {selMenu.x}px; top: {selMenu.y}px" onclick={startCompose}>
		{reviewMode ? 'annotate' : 'comment'}
	</button>
{/if}

<div class="annotatable" class:wide bind:this={colEl}>
	{#each blocks as block (block.id)}
		{@const cards = threadsByBlock.get(block.id) ?? []}
		<section class="ablock" id={block.id}>
			<div class="content" use:contentRef={block.id}>
				<!-- eslint-disable-next-line svelte/no-at-html-tags -- trusted local markdown -->
				{@html block.html}
			</div>
			<button class="addhere" title="Comment on this block" onclick={() => addToBlock(block)}>
				+
			</button>

			{#each cards as t (t.id)}
				<aside
					class="mcard {rootKind(t)}"
					class:focused={focused === t.id}
					use:cardRef={t.id}
					style={wide ? `top:${positions[t.id] ?? 0}px` : ''}
				>
					<div class="chead">
						<span class="kind {rootKind(t)}">{rootKind(t)}</span>
						<span class="state {stateLabel(t)}">{stateLabel(t)}</span>
					</div>
					{#if t.quote}
						<button class="cquote" title="scroll to the highlighted text" onclick={() => scrollToAnchor(t)}>
							{truncate(t.quote)}
						</button>
					{/if}
					{#each t.items as item, i (i)}
						<div class="item">
							<span class="who"
								>{item.author}{item.kind === 'resolve'
									? ' · resolved'
									: item.kind === 'reopen'
										? ' · reopened'
										: item.kind === 'spawn'
											? ' · spawned'
											: ''}</span
							>{item.body}
						</div>
					{/each}
					<textarea placeholder="reply…" bind:value={replyBodies[t.id]}></textarea>
					<div class="row">
						<button disabled={busy} onclick={() => reply(t.id)}>reply</button>
						{#if reviewMode && rootKind(t) === 'question'}
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
				</aside>
			{/each}

			{#if pending?.bid === block.id}
				<aside class="mcard compose" use:cardRef={COMPOSE_ID} style={wide ? `top:${positions[COMPOSE_ID] ?? pending.top}px` : ''}>
					{#if reviewMode}
						<div class="kindpick">
							<button class="linkish" class:on={composeKind === 'comment'} onclick={() => (composeKind = 'comment')}>
								comment
							</button>
							<button class="linkish" class:on={composeKind === 'question'} onclick={() => (composeKind = 'question')}>
								question
							</button>
						</div>
					{/if}
					{#if pending.quote}
						<div class="cquote static">{truncate(pending.quote)}</div>
					{/if}
					<textarea
						placeholder={pending.quote ? 'annotate the selected text…' : 'comment on this block…'}
						bind:value={body}
					></textarea>
					<div class="row">
						<input type="text" bind:value={author} title="author" />
						<button class="primary" disabled={busy || !body.trim()} onclick={comment}>
							{reviewMode ? composeKind : 'comment'}
						</button>
						<button disabled={busy} onclick={cancelCompose}>cancel</button>
					</div>
				</aside>
			{/if}
		</section>
	{/each}
</div>

<style>
	.annotatable {
		position: relative;
	}
	.ablock {
		position: relative;
		scroll-margin-top: 2rem;
		margin-bottom: 0.2rem;
	}
	.content {
		min-width: 0;
	}

	/* the quiet block-level add — brightens on the block you're reading, no hover-to-discover */
	.addhere {
		position: absolute;
		top: 0.15rem;
		right: -1.6rem;
		font: 0.72rem var(--mono);
		border: 1px solid var(--line);
		background: var(--bg);
		color: var(--muted);
		border-radius: 10px;
		padding: 0.05rem 0.45rem;
		cursor: pointer;
		opacity: 0.25;
		transition: opacity 0.12s;
	}
	.ablock:hover .addhere,
	.addhere:focus-visible {
		opacity: 1;
	}
	.addhere:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	/* ---- margin cards ---- */
	.mcard {
		border: 1px solid var(--line);
		border-left: 3px solid var(--accent);
		border-radius: 6px;
		background: var(--panel);
		padding: 0.55rem 0.7rem;
		font: 0.8rem/1.5 var(--sans);
	}
	.mcard.question {
		border-left-color: var(--constraint);
	}
	.mcard.focused {
		box-shadow: 0 0 0 2px var(--accent);
	}

	.chead {
		display: flex;
		gap: 0.5rem;
		align-items: baseline;
		font: 600 0.6rem var(--mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}
	.kind {
		color: var(--muted);
	}
	.kind.question {
		color: var(--constraint);
	}
	.state.open {
		color: var(--accent);
	}
	.state.answered,
	.state.resolved {
		color: var(--resolved);
	}
	.state.spawned {
		color: var(--constraint);
	}

	.cquote {
		display: block;
		width: 100%;
		text-align: left;
		font: italic 0.76rem/1.4 var(--sans);
		color: var(--muted);
		border: none;
		border-left: 2px solid var(--accent);
		background: none;
		padding: 0.1rem 0 0.1rem 0.5rem;
		margin: 0.4rem 0 0.3rem;
		cursor: pointer;
	}
	.cquote:hover {
		color: var(--accent);
	}
	.cquote.static {
		cursor: default;
	}

	.item {
		margin: 0.35rem 0;
		white-space: pre-wrap;
	}
	.item .who {
		font: 0.68rem var(--mono);
		color: var(--muted);
		margin-right: 0.4rem;
	}

	.mcard textarea {
		width: 100%;
		min-height: 2.4rem;
		margin-top: 0.4rem;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--bg);
		color: var(--fg);
		font: 0.8rem/1.5 var(--sans);
		padding: 0.4rem 0.5rem;
		resize: vertical;
	}
	.mcard .row {
		display: flex;
		gap: 0.4rem;
		margin-top: 0.4rem;
		align-items: center;
		flex-wrap: wrap;
	}
	.mcard .row input {
		width: 5.5rem;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--bg);
		color: var(--fg);
		font: 0.72rem var(--mono);
		padding: 0.25rem 0.45rem;
	}
	.mcard .row button {
		font: 0.72rem var(--mono);
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--bg);
		color: var(--fg);
		padding: 0.25rem 0.6rem;
		cursor: pointer;
	}
	.mcard .row button:hover {
		border-color: var(--accent);
	}
	.mcard .row button.primary {
		border-color: var(--accent);
		color: var(--accent);
	}
	.mcard .row button.spawn {
		border-color: var(--constraint);
		color: var(--constraint);
	}
	.kindpick {
		display: flex;
		gap: 0.4rem;
		margin-bottom: 0.4rem;
	}
	.kindpick .linkish {
		font: 0.72rem var(--mono);
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--bg);
		color: var(--fg);
		padding: 0.2rem 0.55rem;
		cursor: pointer;
	}
	.kindpick .linkish.on {
		border-color: var(--accent);
		color: var(--accent);
	}

	/* ---- wide: reserve a right margin (as padding) and float each card into it, absolutely
	   positioned against the whole document so the collision pass has one global coordinate
	   space — cards from adjacent blocks push down past each other, never overlap. ---- */
	@media (min-width: 900px) {
		.annotatable.wide {
			padding-right: 20rem;
		}
		.annotatable.wide .mcard {
			position: absolute;
			right: 0;
			width: 18rem;
		}
	}

	/* ---- narrow: single column, cards flow inline right after their block ---- */
	@media (max-width: 899.98px) {
		.mcard {
			margin: 0.5rem 0 1rem;
		}
		.addhere {
			right: 0;
		}
	}
</style>

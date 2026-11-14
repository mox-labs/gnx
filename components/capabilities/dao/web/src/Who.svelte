<script lang="ts">
	// The organization at a glance: who holds which position, and whose work goes to whom.
	//
	// The nodes are ordinary buttons in two columns, so they wrap, take focus and read aloud
	// like any others. The links are drawn behind them from where the buttons actually sit,
	// and drawn again whenever the map changes size.
	import Mark from './Mark.svelte';
	import { nodeId, type Selection } from './model';
	import { reader } from './reader.svelte';

	const r = reader();

	let box = $state<HTMLElement>();
	let size = $state({ w: 0, h: 0 });
	interface Link {
		id: string;
		kind: string;
		d: string;
		head: string;
	}
	let links = $state<Link[]>([]);
	const placed = new Map<string, HTMLElement>();

	function place(el: HTMLElement, id: string) {
		placed.set(id, el);
		return { destroy: () => void placed.delete(id) };
	}

	function measure(): void {
		if (!box) return;
		const origin = box.getBoundingClientRect();
		const at = (id: string) => {
			const el = placed.get(id);
			if (!el) return null;
			const b = el.getBoundingClientRect();
			return { left: b.left - origin.left, right: b.right - origin.left, y: b.top - origin.top + b.height / 2 };
		};
		const order = new Map(r.book.positions.map((p, i) => [p.name, i]));
		const room = origin.width - Math.max(0, ...[...placed.values()].map((el) => el.getBoundingClientRect().right - origin.left));
		const drawn: Link[] = [];
		for (const e of r.edges) {
			const to = at(`position:${e.to}`);
			if (e.kind === 'holds') {
				const from = at(`participant:${e.from}`);
				if (!from || !to) continue;
				const mid = (from.right + to.left) / 2;
				drawn.push({
					id: e.id,
					kind: e.kind,
					d: `M${from.right} ${from.y}C${mid} ${from.y} ${mid} ${to.y} ${to.left} ${to.y}`,
					head: ''
				});
			} else {
				const from = at(`position:${e.from}`);
				if (!from || !to) continue;
				// Work leaves a little above the middle and arrives a little below, so a position
				// that is both checked and a checker shows two separate ends.
				const y1 = from.y - 5;
				const y2 = to.y + 5;
				const apart = Math.abs((order.get(e.from) ?? 0) - (order.get(e.to) ?? 0));
				const out = Math.max(14, Math.min(room - 6, 14 + 12 * apart));
				const x1 = from.right;
				const x2 = to.right + 7;
				drawn.push({
					id: e.id,
					kind: e.kind,
					d: `M${x1} ${y1}C${x1 + out * 1.33} ${y1} ${x2 + out * 1.33} ${y2} ${x2} ${y2}`,
					head: `M${to.right} ${y2}l7 -4v8z`
				});
			}
		}
		size = { w: origin.width, h: origin.height };
		links = drawn;
	}

	$effect(() => {
		if (!box) return;
		const watch = new ResizeObserver(measure);
		watch.observe(box);
		return () => watch.disconnect();
	});

	const nodeLook = (s: Selection): string => {
		if (!r.lit) return '';
		return r.lit.nodes.has(nodeId(s)) ? 'lit' : 'dim';
	};
	const edgeLook = (id: string): string => (r.lit ? (r.lit.edges.has(id) ? 'lit' : 'dim') : '');
</script>

{#snippet node(s: Selection, note: string, warn: boolean)}
	<button
		type="button"
		class="node {s.kind} {nodeLook(s)}"
		aria-pressed={r.is(s)}
		use:place={nodeId(s)}
		onclick={() => r.toggle(s)}
		onpointerenter={() => (r.hot = s)}
		onpointerleave={() => (r.hot = null)}
		onfocus={() => (r.hot = s)}
		onblur={() => (r.hot = null)}
	>
		<span class="title">
			{#if s.kind === 'participant'}
				<Mark kind={r.book.participants.find((p) => p.name === s.name)?.kind ?? null} />
			{/if}
			{s.name}
		</span>
		{#if note}<span class="note" class:warn>{note}</span>{/if}
	</button>
{/snippet}

<div class="map" bind:this={box}>
	<svg class="links" width={size.w} height={size.h} aria-hidden="true">
		{#each links as link (link.id)}
			<g class="{link.kind} {edgeLook(link.id)}">
				<path d={link.d} fill="none" />
				{#if link.head}<path class="head" d={link.head} />{/if}
			</g>
		{/each}
	</svg>

	<div class="column people">
		<p class="label">Participants</p>
		{#each r.book.participants as p (p.name)}
			{@render node(
				{ kind: 'participant', name: p.name },
				p.positions.length === 0 ? 'holds no position' : '',
				p.positions.length === 0
			)}
		{:else}
			<p class="none">The documents name no participants.</p>
		{/each}
	</div>

	<div class="column posts">
		<p class="label">Positions</p>
		{#each r.book.positions as p (p.name)}
			{@const vacant = p.held_by.length === 0}
			{@const unchecked = p.checked_by.length === 0}
			{@render node(
				{ kind: 'position', name: p.name },
				[vacant ? 'held by no one' : '', unchecked ? 'checked by no one' : ''].filter(Boolean).join(' · '),
				vacant || (unchecked && !p.held_only_by_people)
			)}
		{:else}
			<p class="none">The documents define no positions.</p>
		{/each}
	</div>
</div>

<p class="legend">
	<span><Mark kind="human" /> person</span>
	<span><Mark kind="agent" /> agent</span>
	<span><i class="line holds"></i> holds the position</span>
	<span><i class="line checks"></i> work goes to the position that checks it</span>
</p>

<style>
	.map {
		position: relative;
		display: grid;
		grid-template-columns: minmax(0, 5fr) minmax(2rem, 2fr) minmax(0, 6fr) clamp(2.75rem, 12%, 5rem);
		align-items: start;
	}
	.links {
		position: absolute;
		inset: 0;
		pointer-events: none;
		overflow: visible;
	}
	.links path {
		stroke: var(--dao-line-strong);
		stroke-width: 1.25;
		transition:
			stroke 120ms,
			opacity 120ms;
	}
	.links .checks path {
		stroke: var(--dao-accent);
		stroke-width: 1.5;
	}
	.links .head {
		fill: var(--dao-accent);
		stroke: none;
	}
	.links .lit path {
		stroke-width: 2.25;
	}
	.links .holds.lit path {
		stroke: var(--dao-ink);
	}
	.links .dim {
		opacity: 0.18;
	}
	.column {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		min-width: 0;
	}
	.people {
		grid-column: 1;
	}
	.posts {
		grid-column: 3;
	}
	.label {
		margin: 0 0 0.15rem;
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--dao-muted);
	}
	.none {
		margin: 0;
		font-size: 0.875rem;
		color: var(--dao-muted);
	}
	.node {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		width: 100%;
		min-width: 0;
		text-align: left;
		font: inherit;
		font-size: 0.925rem;
		line-height: 1.3;
		color: var(--dao-ink);
		background: var(--dao-surface);
		border: 1px solid var(--dao-line-strong);
		border-radius: 8px;
		padding: 0.55rem 0.75rem;
		cursor: pointer;
		transition:
			opacity 120ms,
			border-color 120ms;
	}
	.node.participant {
		border-radius: 1.25rem;
		padding-inline: 0.95rem;
	}
	.node:hover,
	.node.lit {
		border-color: var(--dao-ink);
	}
	.node:focus-visible {
		outline: 2px solid var(--dao-accent);
		outline-offset: 2px;
	}
	.node[aria-pressed='true'] {
		border-color: var(--dao-accent);
		box-shadow: inset 0 0 0 1px var(--dao-accent);
	}
	.node.dim {
		opacity: 0.4;
	}
	.title {
		display: flex;
		align-items: center;
		gap: 0.45em;
		font-weight: 600;
		overflow-wrap: break-word;
		min-width: 0;
	}
	.note {
		font-size: 0.75rem;
		color: var(--dao-muted);
	}
	.note.warn {
		color: var(--dao-warn);
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem 1.25rem;
		margin: 1.1rem 0 0;
		font-size: 0.78rem;
		color: var(--dao-muted);
	}
	.legend span {
		display: inline-flex;
		align-items: center;
		gap: 0.45em;
	}
	.line {
		display: inline-block;
		width: 1.4em;
		border-top: 1.5px solid var(--dao-line-strong);
	}
	.line.checks {
		border-color: var(--dao-accent);
	}
	@media (prefers-reduced-motion: reduce) {
		.node,
		.links path {
			transition: none;
		}
	}
	@media print {
		.node.dim,
		.links .dim {
			opacity: 1;
		}
	}
</style>

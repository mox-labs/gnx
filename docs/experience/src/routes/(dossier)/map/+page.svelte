<script lang="ts">
	import { onMount } from 'svelte';
	import '@excalidraw/excalidraw/index.css';
	import type { MapNode } from './+page.server';

	let { data } = $props();

	// The excalidraw canvas is the PRIMARY map surface — the scene file is the
	// artifact (docs/catalog-map/generation-0.excalidraw), rendered and editable
	// here via the real excalidraw editor (React island; edit-in-place, then use
	// its export to save back over the scene file if the changes should persist).
	let xhost: HTMLDivElement;
	let xready = $state(false);
	let xerr = $state('');
	onMount(async () => {
		try {
			const [xc, React, rdc] = await Promise.all([
				import('@excalidraw/excalidraw'),
				import('react'),
				import('react-dom/client')
			]);
			const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
			rdc.createRoot(xhost).render(
				React.createElement(xc.Excalidraw, {
					initialData: {
						// The scene is read off disk as untyped JSON; excalidraw owns the
					// element schema, so borrow its type rather than restate it here.
					elements:
						(data.scene as { elements?: Parameters<typeof xc.restoreElements>[0] })
							?.elements ?? [],
						appState: { viewBackgroundColor: dark ? '#10151a' : '#ffffff' },
						scrollToContent: true
					},
					theme: dark ? 'dark' : 'light'
				})
			);
			xready = true;
		} catch (e) {
			xerr = String(e);
		}
	});
	const SIG: Record<string, string> = { Capability: '□', Agent: '○', Skill: '◇', Flow: '→' };
	const W = 220, H = 52;

	let selected: MapNode | null = $state(null);
	let hot: string | null = $state(null);

	const byTu = $derived(new Map(data.nodes.map((n: MapNode) => [n.tu, n])));
	const neighbors = $derived.by(() => {
		if (!hot) return null;
		const set = new Set([hot]);
		for (const e of data.edges) {
			if (e.s === hot) set.add(e.t);
			if (e.t === hot) set.add(e.s);
		}
		return set;
	});

	function pathFor(e: { s: string; t: string }): string {
		const a = byTu.get(e.s), b = byTu.get(e.t);
		if (!a || !b) return '';
		const down = Math.abs(b.y - a.y) >= Math.abs(b.x - a.x);
		if (down) {
			const y1 = a.y + H / 2, y2 = b.y - H / 2;
			return `M${a.x},${y1} C ${a.x},${(y1 + y2) / 2} ${b.x},${(y1 + y2) / 2} ${b.x},${y2}`;
		}
		const sx = a.x + (b.x > a.x ? W / 2 : -W / 2), tx = b.x + (b.x > a.x ? -W / 2 : W / 2);
		return `M${sx},${a.y} C ${(sx + tx) / 2},${a.y} ${(sx + tx) / 2},${b.y} ${tx},${b.y}`;
	}
	function labelPos(e: { s: string; t: string }) {
		const a = byTu.get(e.s)!, b = byTu.get(e.t)!;
		return { x: (a.x + b.x) / 2 + 8, y: (a.y + b.y) / 2 };
	}
	const edgeHot = (e: { s: string; t: string }) => hot === e.s || hot === e.t;
</script>

<svelte:head><title>catalog map · gnx</title></svelte:head>

<div class="map-page">
	<header>
		<h1>the catalog map — generation 0</h1>
		<p>
			{data.counts.minted} minted · {data.counts.proposed} proposed · {data.counts.flows} flows —
			drawn from the manifests at load, topology derived (GEP-0002/0003). Editable twin:
			<code>docs/catalog-map/generation-0.excalidraw</code>.
		</p>
	</header>

	<div class="xcal" bind:this={xhost}>
		{#if !xready && !xerr}<p class="xnote">loading excalidraw…</p>{/if}
		{#if xerr}<p class="xnote">excalidraw failed to mount: {xerr}</p>{/if}
	</div>

	<h2 class="derived-h">derived live view — from the manifests at load</h2>
	<div class="canvas" class:hashot={hot !== null}>
		<svg viewBox={`0 0 1240 ${data.height}`} role="img" aria-label="gnx component map">
			<defs>
				<marker id="marr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
					<path d="M0,0.6 L7.4,4 L0,7.4 z" fill="context-stroke" />
				</marker>
				<!-- tufte #5: uses is directional — lighter arrowhead, open form -->
				<marker id="marr-soft" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
					<path d="M0.5,1 L7,4 L0.5,7" fill="none" stroke="context-stroke" stroke-width="1.2" />
				</marker>
			</defs>

			{#each data.hulls as h (h.label)}
				<rect class="hull" class:band={h.band} x={h.x} y={h.y} width={h.w} height={h.h} rx="14" />
				<text class="hull-label" x={h.x + 10} y={h.y + 22}>{h.label}</text>
			{/each}

			{#each data.edges as e, i (i)}
				{#if !e.xcat || edgeHot(e)}
					<path class="edge" class:uses={e.uses} class:hot={edgeHot(e)} d={pathFor(e)}
						marker-end={e.uses ? 'url(#marr-soft)' : 'url(#marr)'} />
					{#if e.port}
						<text class="edge-label" class:hot={edgeHot(e)} x={labelPos(e).x} y={labelPos(e).y}>{e.port}</text>
					{/if}
				{/if}
			{/each}

			{#each data.nodes as n (n.tu)}
				<g class="node" class:proposed={n.status === 'proposed'} class:dim={neighbors && !neighbors.has(n.tu)}
					class:sel={selected?.tu === n.tu}
					role="button" tabindex="0" aria-label={`${n.tu} — ${n.kind}, ${n.status}`}
					onmouseenter={() => (hot = n.tu)} onmouseleave={() => (hot = null)}
					onclick={() => (selected = n)}
					onkeydown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selected = n; } }}>
					<rect x={n.x - W / 2} y={n.y - H / 2} width={W} height={H} rx="6" />
					<text class="sig" x={n.x - W / 2 + 12} y={n.y + 5}>{SIG[n.kind] ?? '⬡'}</text>
					<text class="name" x={n.x - W / 2 + 34} y={n.y - 2}>{n.id}</text>
					<text class="ns" x={n.x - W / 2 + 34} y={n.y + 15}>{n.ns} · {n.maturity}</text>
					{#if n.flags.length}<text class="flag" x={n.x + W / 2 - 20} y={n.y - 8}>⚠</text>{/if}
				</g>
			{/each}
		</svg>
	</div>

	{#if selected}
		<aside class="detail">
			<button class="close" onclick={() => (selected = null)}>×</button>
			<h2>{SIG[selected.kind] ?? '⬡'} {selected.tu}</h2>
			<p class="meta">{selected.kind} · {selected.status} · {selected.maturity}</p>
			{#if selected.description}<p class="desc">{selected.description}</p>{/if}
			<dl>
				{#if selected.ports.length}<dt>ports</dt><dd>{#each selected.ports as p (p)}<code>{p}</code>{/each}</dd>{/if}
				{#if selected.tags.length}<dt>tags</dt><dd>{#each selected.tags as t (t)}<code class="tag">{t}</code>{/each}</dd>{/if}
				{#if selected.requires.length}<dt>requires</dt><dd>{#each selected.requires as r (r)}<code>{r}</code>{/each}</dd>{/if}
				{#if selected.uses.length}<dt>uses</dt><dd>{#each selected.uses as u (u)}<code>{u}</code>{/each}</dd>{/if}
				{#if selected.flags.length}<dt>open</dt><dd class="warn">{selected.flags.join(' · ')}</dd>{/if}
			</dl>
		</aside>
	{/if}
</div>

<style>
	.map-page { padding: 1rem 1.5rem 3rem; position: relative; }
	.xcal { height: 74vh; min-height: 480px; border: 1px solid var(--line, #31414d); border-radius: 10px; overflow: hidden; margin-bottom: 1.4rem; }
	.xnote { color: var(--muted, #8b98a4); font: 0.8rem 'IBM Plex Mono', monospace; padding: 1rem; }
	.derived-h { font: 600 0.8rem 'IBM Plex Mono', monospace; color: var(--muted, #8b98a4); letter-spacing: 0.1em; text-transform: uppercase; margin: 0 0 0.5rem; }
	header h1 { font-family: 'IBM Plex Mono', monospace; font-size: 1.05rem; margin: 0 0 0.2rem; }
	header p { color: var(--muted, #8b98a4); font-size: 0.82rem; max-width: 68ch; margin: 0 0 1rem; }
	header code { font-size: 0.75rem; }
	.canvas { border: 1px solid var(--line, #31414d); border-radius: 10px; overflow-x: auto; background: var(--surface, transparent); }
	svg { display: block; min-width: 1100px; width: 100%; height: auto; }
	/* tufte #2: containment is a filled panel, not another dash pattern —
	   dashes stay reserved for the maturity channel on node strokes */
	.hull { fill: currentColor; fill-opacity: 0.05; stroke: var(--line, #3d4e5b); stroke-width: 1; }
	.hull.band { fill-opacity: 0.025; stroke: none; }
	.hull-label { fill: var(--muted, #8b98a4); font: 11px 'IBM Plex Mono', monospace; }
	.edge { fill: none; stroke: var(--line, #31414d); stroke-width: 1.6; }
	.edge.uses { stroke-dasharray: 2.5 4; stroke-width: 1.2; }
	.edge.hot { stroke: var(--accent, #d9a441); stroke-width: 2.2; }
	.edge-label { fill: var(--faint, #5d6a75); font: 9.5px 'IBM Plex Mono', monospace; }
	.edge-label.hot { fill: var(--accent, #d9a441); }
	.hashot .edge:not(.hot) { opacity: 0.15; }
	.node { cursor: pointer; }
	.node rect { fill: var(--panel, #1b242c); stroke: currentColor; stroke-width: 1.5; }
	.node.proposed rect { stroke-dasharray: 5 4; stroke: var(--muted, #8b98a4); }
	.node.dim { opacity: 0.25; }
	.node.sel rect, .node:focus rect { stroke: var(--accent, #d9a441); stroke-width: 2.2; outline: none; }
	.node text { fill: currentColor; }
	.node .sig { font: 13px 'IBM Plex Mono', monospace; opacity: 0.7; }
	.node .name { font: 600 12.5px 'IBM Plex Mono', monospace; }
	.node .ns { font: 9.5px 'IBM Plex Mono', monospace; opacity: 0.5; }
	.node .flag { fill: var(--warn, #d06a5f); font-size: 12px; }
	@media (prefers-reduced-motion: no-preference) {
		.node, .edge, .edge-label { transition: opacity 0.15s ease, stroke 0.15s ease; }
	}
	.detail { position: fixed; right: 1rem; top: 5rem; width: min(360px, 90vw); background: var(--panel, #181f26);
		border: 1px solid var(--line, #31414d); border-radius: 10px; padding: 1rem 1.1rem; z-index: 30; }
	.detail .close { position: absolute; top: 0.4rem; right: 0.6rem; background: none; border: none;
		color: var(--muted, #8b98a4); font-size: 1.1rem; cursor: pointer; }
	.detail h2 { font: 600 0.85rem 'IBM Plex Mono', monospace; margin: 0 1rem 0.1rem 0; word-break: break-all; }
	.detail .meta { color: var(--muted, #8b98a4); font-size: 0.75rem; margin: 0 0 0.5rem; }
	.detail .desc { font-size: 0.82rem; margin: 0 0 0.4rem; }
	.detail dt { color: var(--faint, #5d6a75); font-size: 0.65rem; letter-spacing: 0.12em; text-transform: uppercase; margin-top: 0.5rem; }
	.detail dd { margin: 0.1rem 0 0; display: flex; flex-wrap: wrap; gap: 0.25rem; }
	.detail code { font-size: 0.72rem; border: 1px solid var(--line, #31414d); border-radius: 5px; padding: 0.05rem 0.4rem; }
	.detail code.tag { border-style: dashed; }
	.detail .warn { color: var(--warn, #d06a5f); font-size: 0.78rem; }
</style>

<script lang="ts">
	import { base } from '$app/paths';
	import { MARKETPLACE_REF } from '$lib/catalog';

	let { data } = $props();

	const p = $derived(data.plugin);
</script>

<svelte:head>
	<title>{p.name} plugin · gnx</title>
	{#if p.description}<meta name="description" content={p.description} />{/if}
</svelte:head>

<article class="doc plugin">
	<div class="doc-meta"><span class="kind">Plugin</span></div>

	<h1>{p.name}</h1>
	{#if p.description}<p class="lede">{p.description}</p>{/if}

	<h2>Install</h2>
	<p>In Claude Code:</p>
	<pre><code>/plugin marketplace add {MARKETPLACE_REF}
/plugin install {p.name}@gnx</code></pre>
	<p>From a terminal:</p>
	<pre><code>claude plugin marketplace add {MARKETPLACE_REF}
claude plugin install {p.name}@gnx</code></pre>
	<p class="gloss">
		Adding the marketplace is once per machine. <a href="{base}/docs/install-a-plugin">Install a
		plugin</a> says what each command does.
	</p>

	<h2>What it bundles</h2>
	<table>
		<thead>
			<tr>
				<th>component</th>
				<th>kind</th>
				<th>what it is</th>
			</tr>
		</thead>
		<tbody>
			{#each data.members as m (m.name)}
				<tr>
					<td>
						{#if m.entry}
							<a href="{base}/components/{m.entry.slug}"><code>{m.name}</code></a>
						{:else}
							<code>{m.name}</code>
						{/if}
					</td>
					<td>
						{m.kind}{#if m.entry && m.entry.kind === 'Capability'}, from the
							<a href="{base}/components/{m.entry.slug}"><code>{m.entry.slug}</code></a> package{/if}
					</td>
					<td class="desc">{m.entry?.description ?? ''}</td>
				</tr>
			{/each}
		</tbody>
	</table>

	<nav class="pager">
		<a href="{base}/catalog">← The catalog</a>
	</nav>
</article>

<style>
	.doc-meta {
		margin-bottom: 1.5rem;
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.doc-meta .kind {
		opacity: 0.6;
		font-weight: 600;
	}
	.lede,
	.gloss,
	.desc {
		color: var(--muted);
	}
	.gloss {
		font-size: 0.85rem;
	}
	table {
		font-size: 0.85rem;
	}
	@media (max-width: 640px) {
		table,
		thead,
		tbody,
		tr,
		td {
			display: block;
		}
		thead {
			display: none;
		}
		td {
			border: none;
			padding: 0.1rem 0;
		}
		tr {
			border-bottom: 1px solid var(--line);
			padding: 0.6rem 0;
		}
	}
</style>

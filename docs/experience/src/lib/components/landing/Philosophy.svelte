<script lang="ts">
	// West panel — what gnx is, and the two things it does for an agent's work. The shared
	// thesis: more effective, and inspectable. Each claim names the mechanism, not a slogan.
</script>

<section class="philosophy" aria-labelledby="what-gnx-is">
	<h2 id="what-gnx-is" class="label">What gnx is</h2>
	<p class="lede">
		Tools for building with agents: Python packages you install with <code>uv</code>, and skills
		you install into Claude Code as plugins.
	</p>
	<dl>
		<dt>More effective</dt>
		<dd>
			A tool teaches itself to an agent: <code>ix --skill</code> prints the guide for the version
			installed. Every setting has a safe default, and a refusal says what to do next.
		</dd>
		<dt>Inspectable</dt>
		<dd>
			<code>ix plan</code> shows each setting and where it came from, before anything runs. A run
			is recorded as it goes. ix answers with an interval, and refuses a verdict the evidence
			can't carry.
		</dd>
	</dl>
</section>

<style>
	.philosophy {
		max-width: 24rem;
		font-family: var(--font-mono);
	}
	.label {
		font: 600 0.68rem var(--font-sans);
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: var(--muted);
		margin: 0 0 var(--space-1);
	}
	.lede {
		font-size: var(--type-base);
		line-height: 1.45;
		color: var(--fg);
		margin: 0 0 var(--space-2);
	}
	.lede code,
	dd code {
		font-size: 0.92em;
		color: var(--color-code);
	}
	dl {
		margin: 0;
		border-left: 2px solid var(--accent);
		padding-left: var(--space-2);
	}
	dt {
		font-size: var(--type-sm);
		color: var(--fg);
		font-weight: 600;
	}
	dd {
		font-size: 0.85rem;
		line-height: 1.6;
		color: var(--muted);
		margin: 0 0 var(--space-1);
	}
	dd:last-child {
		margin-bottom: 0;
	}
	@media (max-width: 768px) {
		.philosophy {
			max-width: 26rem;
			text-align: left;
		}
	}
</style>

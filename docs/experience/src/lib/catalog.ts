// The catalog's shapes and the pure helpers over them, shared by the server loaders and the
// pages that render them. Reading the disk lives in $lib/server/catalog; nothing here touches
// it, so a page can import this file.
import type { Compiled } from '$lib/dag';

/** Where the source is browsed, and the marketplace a person adds. */
export const REPO_URL = 'https://github.com/mox-labs/gnx';
export const MARKETPLACE_REF = 'mox-labs/gnx';
const SOURCE_REF = 'main';

/** GitHub view of a repo-relative path (a directory or a file; GitHub redirects as needed). */
export function sourceUrl(repoPath: string): string {
	return `${REPO_URL}/blob/${SOURCE_REF}/${repoPath}`;
}

export interface CatalogEntry {
	slug: string;
	kind: string;
	/** One line: the overview's `summary`, else the manifest's, else the package's or body's. */
	description: string;
	/** Repo-relative directory, for the source link. */
	dir: string;
	/** Repo-relative path of the overview, when one exists. */
	overview: string | null;
	/** Repo-relative path of the README, when one exists. */
	readme: string | null;
	/** Present only where the component carries a manifest.yaml. */
	type_url: string | null;
	namespace: string | null;
	provides: string[];
	requires: string[];
	/** The subset of `provides` / `requires` that are ports — see `isPort`. */
	produces: string[];
	consumes: string[];
	relations: Record<string, string[]>;
	maturity: 'shipped' | 'designed';
	/** Which installable plugins ship this component. */
	plugins: string[];
	/**
	 * For a Capability: the plugins that ship its *skill*, if any. A capability's runnable
	 * does not project — it is a Python package reached over a transport — so borrowing its
	 * skill's membership into `plugins` would claim you can install the package. You cannot.
	 */
	surfaceIn: string[];
}

export interface PluginMember {
	/** The name the bundle declares. */
	name: string;
	kind: 'Skill' | 'Agent';
	/** The catalog component this member is, or comes from (a capability's own skill). */
	component: string | null;
}

export interface PluginEntry {
	name: string;
	description: string;
	version: string;
	category: string;
	keywords: string[];
	agents: number;
	skills: number;
	members: PluginMember[];
}

export interface Catalog {
	entries: CatalogEntry[];
	plugins: PluginEntry[];
	composition: Compiled | null;
	manifested: number;
}

/** The plugins a component ships in, or whose skill it provides. */
export function shippedIn(entry: CatalogEntry): string[] {
	return entry.kind === 'Capability' ? entry.surfaceIn : entry.plugins;
}

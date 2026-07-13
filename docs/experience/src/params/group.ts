import type { ParamMatcher } from '@sveltejs/kit';
import { groupById } from '$lib/registers';

// /dossier/<group>/<slug> — the private wing's path families, declared in one place
// ($lib/registers). Concept folios stay at /dossier/<slug>, one level up.
export const match: ParamMatcher = (param) => groupById(param) !== undefined;

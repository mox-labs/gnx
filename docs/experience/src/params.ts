import { defineParams } from '@sveltejs/kit/params';
import { groupById } from '#lib/registers.ts';

export const params = defineParams({
	// /dossier/<group>/<slug> — the private wing's path families, declared in one place
	// (#lib/registers). Concept folios stay at /dossier/<slug>, one level up.
	group: (param) => (groupById(param) !== undefined ? param : undefined)
});

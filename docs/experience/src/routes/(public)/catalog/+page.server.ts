import type { PageServerLoad } from './$types';
import { loadCatalog } from '$lib/server/catalog';

// The catalog reads what is on disk; $lib/server/catalog says from where.
export const load: PageServerLoad = () => loadCatalog();

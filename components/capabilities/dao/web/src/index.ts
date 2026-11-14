// Registers <dao-book>. Loading this file twice, or beside another copy, defines it once.
import DaoBook from './DaoBook.svelte';

const TAG = 'dao-book';
const element = (DaoBook as unknown as { element: CustomElementConstructor }).element;
if (!customElements.get(TAG)) customElements.define(TAG, element);

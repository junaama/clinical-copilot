/**
 * Display-only helpers for synthetic OpenEMR/Synthea names.
 */

const SYNTHETIC_NAME_TOKEN = /\b([A-Za-zÀ-ÖØ-öø-ÿ'’-]+)\d+\b/g;

export function cleanSyntheticNameSuffixes(name: string): string {
  return name.replace(SYNTHETIC_NAME_TOKEN, '$1').replace(/\s+/g, ' ').trim();
}

export function formatPanelPatientName(
  givenName: string,
  familyName: string,
): string {
  return cleanSyntheticNameSuffixes(`${givenName} ${familyName}`);
}

export function formatPanelPatientListName(
  givenName: string,
  familyName: string,
): string {
  const given = cleanSyntheticNameSuffixes(givenName);
  const family = cleanSyntheticNameSuffixes(familyName);

  return [family, given].filter(Boolean).join(', ');
}

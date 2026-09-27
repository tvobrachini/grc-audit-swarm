// Control frameworks the RACM maps to (mirrors src/api/models.py
// DEFAULT_FRAMEWORKS and the selectable list in NewAuditModal). Auditing
// standards (IIA, PCAOB) govern the auditor, not the entity's controls, so
// they are never listed here — see AUDITING_STANDARDS below.
const FRAMEWORK_REFS: Record<string, { title: string; desc: string; url: string }> = {
  "COSO 2013": {
    title: "COSO 2013 Internal Control – Integrated Framework",
    desc: "The 17 principles organized under the five components of internal control.",
    url: "https://www.coso.org/",
  },
  "NIST SP 800-53": {
    title: "NIST SP 800-53",
    desc: "Security and Privacy Controls for Information Systems and Organizations",
    url: "https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final",
  },
  "CIS Controls": {
    title: "CIS Critical Security Controls",
    desc: "Prioritized safeguards to mitigate common cyber-attacks",
    url: "https://www.cisecurity.org/controls",
  },
  "ISO 27001": {
    title: "ISO/IEC 27001:2022",
    desc: "Information security management systems requirements",
    url: "https://www.iso.org/standard/27001",
  },
  "SOC 2": {
    title: "AICPA SOC 2",
    desc: "Service Organization Controls — Trust Services Criteria",
    url: "https://www.aicpa.org/",
  },
};

/**
 * Auditing standards that inspired this tool's design (not control
 * frameworks, and not a claim of conformance with them).
 */
const AUDITING_STANDARDS = [
  {
    title: "IIA Global Internal Audit Standards 12.3",
    desc: "Engagement planning, informing the RACM's planning phase.",
  },
  {
    title: "IIA Global Internal Audit Standards 14.6",
    desc: "Communicating engagement results, informing the reporting phase.",
  },
];

interface Props {
  frameworks: string[];
}

export function FrameworkPane({ frameworks }: Props) {
  const refs = frameworks
    .map((f) => ({ key: f, info: FRAMEWORK_REFS[f] }))
    .filter((r) => r.info);

  return (
    <div className="space-y-3">
      {refs.map(({ key, info }) => (
        <div
          key={key}
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3"
        >
          <p className="text-xs font-medium text-[var(--color-text-primary)]">
            {info.title}
          </p>
          <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">
            {info.desc}
          </p>
        </div>
      ))}
      {refs.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)] opacity-50">
          No framework references
        </p>
      )}

      <div className="pt-2">
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Auditing standards that inspired the design
        </p>
        <p className="mb-2 text-[10px] text-[var(--color-text-muted)]">
          These govern how the audit is conducted, not the entity's controls —
          they are not control frameworks, and this tool makes no claim of
          conformance with them.
        </p>
        <div className="space-y-2">
          {AUDITING_STANDARDS.map((s) => (
            <div
              key={s.title}
              className="rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-bg-base)]/50 p-3"
            >
              <p className="text-xs font-medium text-[var(--color-text-secondary)]">
                {s.title}
              </p>
              <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">{s.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

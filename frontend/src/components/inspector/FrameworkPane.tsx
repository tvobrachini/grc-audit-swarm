const FRAMEWORK_REFS: Record<string, { title: string; desc: string; url: string }> = {
  COSO: {
    title: "COSO ERM Framework",
    desc: "Enterprise Risk Management — Integrating with Strategy and Performance (2017)",
    url: "https://www.coso.org/",
  },
  PCAOB: {
    title: "PCAOB AS 2201",
    desc: "Auditing Standard No. 2201 — Integrated Audit of Internal Control",
    url: "https://pcaobus.org/",
  },
  IIA: {
    title: "IIA Standards",
    desc: "International Standards for the Professional Practice of Internal Auditing (2024)",
    url: "https://www.theiia.org/",
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
    </div>
  );
}

import { useState } from "react";
import { Layers, Lock, LockOpen, Plus, Trash2 } from "lucide-react";

import { TIER_NUMBERS, type KeyValue } from "@/utils/registrationApi";

const CELL =
  "h-8 w-full rounded border border-transparent bg-transparent px-2 text-[#eaf0f7] outline-none placeholder:text-[#57606c] focus:border-[#20abc7]";

/** A container's environment: a value for every deployment, per tier where it
 *  differs, and secret values kept by the secret provider rather than the template. */
export default function EnvironmentEditor({
  rows,
  ids,
  onAdd,
  onRemove,
  onChange,
  secretsAvailable,
  copying,
}: {
  rows: KeyValue[];
  ids: string[];
  onAdd: () => void;
  onRemove: (index: number) => void;
  onChange: (index: number, patch: Partial<KeyValue>) => void;
  /** Whether this server can store secrets (null while asking). */
  secretsAvailable: boolean | null;
  /** A copy: stored secret values are not copied, so they are asked for again. */
  copying: boolean;
}) {
  const [tiersOpen, setTiersOpen] = useState<Record<string, boolean>>({});

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-[15px] font-bold text-[#eef2f6]">Environment Variables</h4>
        <button
          type="button"
          onClick={onAdd}
          className="flex items-center gap-1 text-[12.5px] font-semibold text-[#20abc7] hover:text-[#4dc6dd]"
        >
          <Plus className="h-3.5 w-3.5" />
          Add Variable
        </button>
      </div>
      <div className="overflow-hidden rounded-[8px] border border-[#1c2836]">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <th className="border-b border-[#1c2836] px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-wide text-[#8b97a5]">
                Key
              </th>
              <th className="border-b border-[#1c2836] px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-wide text-[#8b97a5]">
                Value
              </th>
              <th className="w-24 border-b border-[#1c2836]" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const id = ids[i] ?? String(i);
              const named = row.key.trim() || `variable ${i + 1}`;
              const hasTierValues = Object.values(row.tierValues).some((v) => v !== "");
              const showTiers = !row.secret && (tiersOpen[id] ?? hasTierValues);
              const canBeSecret = secretsAvailable === true || row.secret;
              return (
                <tr key={id} className="border-b border-[#1c2836] align-top last:border-b-0">
                  <td className="p-1.5">
                    <input
                      aria-label="Variable name"
                      value={row.key}
                      onChange={(e) => onChange(i, { key: e.target.value })}
                      className={`${CELL} font-mono`}
                    />
                  </td>
                  <td className="p-1.5">
                    <input
                      aria-label={`Value of ${named}`}
                      type={row.secret ? "password" : "text"}
                      autoComplete={row.secret ? "new-password" : "off"}
                      placeholder={
                        row.secret
                          ? row.secretId && !copying
                            ? "Unchanged — type a new value to replace it"
                            : "Secret value"
                          : undefined
                      }
                      value={row.value}
                      onChange={(e) => onChange(i, { value: e.target.value })}
                      className={CELL}
                    />
                    {showTiers && (
                      <div className="mt-1.5 grid grid-cols-2 gap-1.5 px-1">
                        {TIER_NUMBERS.map((tier) => (
                          <label key={tier} className="flex items-center gap-1.5 text-[10.5px] text-[#57606c]">
                            <span className="w-10 shrink-0">Tier {tier}</span>
                            <input
                              aria-label={`Tier ${tier} value of ${named}`}
                              placeholder="Default"
                              value={row.tierValues[tier] ?? ""}
                              onChange={(e) =>
                                onChange(i, { tierValues: { ...row.tierValues, [tier]: e.target.value } })
                              }
                              className={`${CELL} h-7 border-[#1c2836] text-[12px]`}
                            />
                          </label>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center justify-end gap-2.5">
                      <button
                        type="button"
                        disabled={row.secret}
                        onClick={() => setTiersOpen((open) => ({ ...open, [id]: !showTiers }))}
                        className="text-[#8b97a5] hover:text-[#20abc7] disabled:opacity-30"
                        aria-label={`Different value of ${named} per tier`}
                        aria-pressed={showTiers}
                        title="Different value per tier"
                      >
                        <Layers className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        disabled={!canBeSecret}
                        onClick={() => onChange(i, { secret: !row.secret, tierValues: {} })}
                        className={`hover:text-[#20abc7] disabled:opacity-30 ${row.secret ? "text-[#fbbf24]" : "text-[#8b97a5]"}`}
                        aria-label={`${named} is secret`}
                        aria-pressed={row.secret}
                        title={
                          canBeSecret
                            ? "Secret: kept by the secret provider, never shown again"
                            : "Secret storage isn't configured on this server (CONTAINER_SECRET_PROVIDER_URL)"
                        }
                      >
                        {row.secret ? <Lock className="h-3.5 w-3.5" /> : <LockOpen className="h-3.5 w-3.5" />}
                      </button>
                      <button
                        type="button"
                        onClick={() => onRemove(i)}
                        className="text-[#8b97a5] hover:text-[#f0655f]"
                        aria-label={`Remove ${named}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={3} className="px-4 py-5 text-center text-[12px] text-[#57606c]">
                  No environment variables yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-start gap-2 rounded-[8px] border border-[rgba(59,130,246,0.35)] bg-[rgba(59,130,246,0.08)] p-3 text-[12.5px] text-[#93c5fd]">
        <span className="mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#3b82f6] text-[10px] font-bold text-white">
          i
        </span>
        <span>
          Set on the container for every deployment of this application; the layers button gives a
          variable a different value on some tiers, and the lock keeps a value secret.
          {secretsAvailable === false &&
            " Secret storage isn't configured on this server, so secret values can't be kept yet."}
        </span>
      </div>
    </div>
  );
}

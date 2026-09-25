import { useState } from "react";
import { Download, Layers, Loader2, Plus, Trash2 } from "lucide-react";

import {
  ALWAYS_SENT,
  C2AI_FILLED,
  TIER_NUMBERS,
  choices,
  type ParamType,
  type ParameterDef,
} from "@/utils/registrationApi";
import { FIELD_CLASS, Select } from "./wizardStyles";

const PARAM_TYPE_LABEL: Record<ParamType, string> = {
  text: "Text",
  number: "Number",
  boolean: "Boolean",
  select: "Choice",
  "key-value": "Key-Value",
};

const SMALL_FIELD = `${FIELD_CLASS} h-8 text-[12px]`;

/** A text, number, boolean or choice value, typed the way the parameter takes it. */
function ValueInput({
  parameter,
  value,
  onChange,
  label,
  blank,
}: {
  parameter: ParameterDef;
  value: string;
  onChange: (value: string) => void;
  label: string;
  /** What an empty value means here. */
  blank: string;
}) {
  if (parameter.type === "boolean") {
    return (
      <Select aria-label={label} className="h-8 text-[12px]" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{blank}</option>
        <option value="true">True</option>
        <option value="false">False</option>
      </Select>
    );
  }
  if (parameter.type === "select") {
    return (
      <Select aria-label={label} className="h-8 text-[12px]" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{blank}</option>
        {choices(parameter.options).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </Select>
    );
  }
  return (
    <input
      aria-label={label}
      type={parameter.type === "number" ? "number" : "text"}
      placeholder={blank}
      className={SMALL_FIELD}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** A GitHub workflow's parameters: what each is called, takes and starts
 *  as, per tier where it differs. The deploy form is generated from these. */
export default function ParameterEditor({
  rows,
  ids,
  onAdd,
  onRemove,
  onChange,
  onImport,
  importing,
}: {
  rows: ParameterDef[];
  ids: string[];
  onAdd: () => void;
  onRemove: (index: number) => void;
  onChange: (index: number, patch: Partial<ParameterDef>) => void;
  onImport: () => void;
  importing: boolean;
}) {
  // Rows whose per-tier values are open; open from the start when set.
  const [tiersOpen, setTiersOpen] = useState<Record<string, boolean>>({});

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <h4 className="text-[15px] font-bold text-[#eef2f6]">Parameters</h4>
        <div className="flex items-center gap-4">
          <button
            type="button"
            onClick={onImport}
            disabled={importing}
            className="flex items-center gap-1 text-[12.5px] font-semibold text-[#20abc7] hover:text-[#4dc6dd] disabled:opacity-60"
          >
            {importing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
            Import from workflow
          </button>
          <button
            type="button"
            onClick={onAdd}
            className="flex items-center gap-1 text-[12.5px] font-semibold text-[#20abc7] hover:text-[#4dc6dd]"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Parameter
          </button>
        </div>
      </div>

      {rows.length === 0 && (
        <p className="rounded-[8px] border border-[#1c2836] px-4 py-5 text-center text-[12px] text-[#57606c]">
          No parameters yet. Import them from the workflow file, or add them one by one.
        </p>
      )}

      <div className="space-y-2">
        {rows.map((row, i) => {
          const id = ids[i] ?? String(i);
          const key = row.name.trim();
          const filledBy = C2AI_FILLED[key];
          const named = key || `parameter ${i + 1}`;
          const hasTierValues = Object.values(row.tierValues).some((v) => v !== "");
          const showTiers = row.type !== "key-value" && (tiersOpen[id] ?? hasTierValues);
          return (
            <div key={id} className="space-y-2 rounded-[8px] border border-[#1c2836] p-2.5">
              <div className="flex items-center gap-2">
                <input
                  aria-label="Parameter name"
                  value={row.name}
                  onChange={(e) => onChange(i, { name: e.target.value })}
                  placeholder="param_name"
                  className={`${SMALL_FIELD} flex-1 font-mono`}
                />
                <Select
                  aria-label={`Type of ${named}`}
                  className="h-8 w-[120px] text-[12px]"
                  value={row.type}
                  onChange={(e) =>
                    onChange(i, { type: e.target.value as ParamType, value: "", tierValues: {} })
                  }
                >
                  {(Object.keys(PARAM_TYPE_LABEL) as ParamType[]).map((t) => (
                    <option key={t} value={t}>
                      {PARAM_TYPE_LABEL[t]}
                    </option>
                  ))}
                </Select>
                {!filledBy && (
                  <label className="flex shrink-0 items-center gap-1.5 text-[11.5px] text-[#8b97a5]">
                    <input
                      type="checkbox"
                      aria-label={`${named} is required`}
                      checked={row.required}
                      onChange={(e) => onChange(i, { required: e.target.checked })}
                    />
                    Required
                  </label>
                )}
                <button
                  type="button"
                  onClick={() => onRemove(i)}
                  className="text-[#8b97a5] hover:text-[#f0655f]"
                  aria-label={`Remove ${named}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>

              {filledBy ? (
                <p className="text-[11.5px] text-[#93c5fd]">
                  C2AI fills this in: {filledBy}.{" "}
                  {ALWAYS_SENT.has(key)
                    ? "It is sent to every workflow anyway, so this parameter can be removed."
                    : "Nobody is asked for it when deploying."}
                </p>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      aria-label={`Label of ${named}`}
                      placeholder="Label (optional)"
                      className={SMALL_FIELD}
                      value={row.label}
                      onChange={(e) => onChange(i, { label: e.target.value })}
                    />
                    {row.type === "key-value" ? (
                      <div className="grid grid-cols-2 gap-2">
                        <input
                          aria-label={`Default key of ${named}`}
                          placeholder="Default key"
                          className={SMALL_FIELD}
                          value={row.kvKey}
                          onChange={(e) => onChange(i, { kvKey: e.target.value })}
                        />
                        <input
                          aria-label={`Default value of ${named}`}
                          placeholder="Default value"
                          className={SMALL_FIELD}
                          value={row.kvValue}
                          onChange={(e) => onChange(i, { kvValue: e.target.value })}
                        />
                      </div>
                    ) : (
                      <ValueInput
                        parameter={row}
                        label={`Default of ${named}`}
                        blank="No default"
                        value={row.value}
                        onChange={(value) => onChange(i, { value })}
                      />
                    )}
                  </div>
                  <input
                    aria-label={`Help text of ${named}`}
                    placeholder="Help text shown when deploying (optional)"
                    className={SMALL_FIELD}
                    value={row.description}
                    onChange={(e) => onChange(i, { description: e.target.value })}
                  />
                  {row.type === "select" && (
                    <input
                      aria-label={`Choices of ${named}`}
                      placeholder="Choices, separated by commas: small, medium, large"
                      className={SMALL_FIELD}
                      // Kept as typed (split, not trimmed) so a comma can be typed.
                      value={row.options.join(",")}
                      onChange={(e) => onChange(i, { options: e.target.value.split(",") })}
                    />
                  )}
                  {row.type !== "key-value" && (
                    <button
                      type="button"
                      onClick={() => setTiersOpen((open) => ({ ...open, [id]: !showTiers }))}
                      className="flex items-center gap-1 text-[11.5px] font-medium text-[#20abc7] hover:text-[#4dc6dd]"
                    >
                      <Layers className="h-3 w-3" />
                      {showTiers ? "Same value on every tier" : "Different value per tier"}
                    </button>
                  )}
                  {showTiers && (
                    <div className="grid grid-cols-4 gap-2">
                      {TIER_NUMBERS.map((tier) => (
                        <div key={tier}>
                          <p className="mb-0.5 text-[10.5px] text-[#57606c]">Tier {tier}</p>
                          <ValueInput
                            parameter={row}
                            label={`Tier ${tier} value of ${named}`}
                            blank="Default"
                            value={row.tierValues[tier] ?? ""}
                            onChange={(value) =>
                              onChange(i, { tierValues: { ...row.tierValues, [tier]: value } })
                            }
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-3 flex items-start gap-2 rounded-[8px] border border-[rgba(59,130,246,0.35)] bg-[rgba(59,130,246,0.08)] p-3 text-[12.5px] text-[#93c5fd]">
        <span className="mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#3b82f6] text-[10px] font-bold text-white">
          i
        </span>
        <span>
          Each parameter becomes a field on the deployment form, filled in with its default (or
          its value for the tier being deployed to), and is sent to the workflow as an input.
        </span>
      </div>
    </div>
  );
}

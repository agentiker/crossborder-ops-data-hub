import { useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { RotateCcw, Pencil } from "lucide-react";
import { api, type BizConfigRow } from "@/api";
import { useMe } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

// 退货率等以「%」为单位的项：后端存小数（0.05），展示/输入按百分数（5）。其余项原值直显。
const isPct = (r: BizConfigRow) => r.unit === "%";
const toDisplay = (r: BizConfigRow, v: number) => (isPct(r) ? v * 100 : v);
const fromDisplay = (r: BizConfigRow, v: number) => (isPct(r) ? v / 100 : v);
const fmtNum = (n: number) =>
  Number.isInteger(n) ? String(n) : String(Number(n.toFixed(4)));

export function BizConfigPage() {
  const me = useMe();
  const [rows, setRows] = useState<BizConfigRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [edit, setEdit] = useState<BizConfigRow | null>(null);
  const [flashKey, setFlashKey] = useState<string | null>(null);

  useEffect(() => {
    if (!me || !me.is_boss) return;
    api
      .bizConfigs()
      .then((r) => setRows(r.items))
      .catch((e) => setLoadError(String(e instanceof Error ? e.message : e)));
  }, [me]);

  useEffect(() => {
    if (!flashKey) return;
    const t = setTimeout(() => setFlashKey(null), 2000);
    return () => clearTimeout(t);
  }, [flashKey]);

  const groups = useMemo(() => {
    if (!rows) return [];
    const by = new Map<string, BizConfigRow[]>();
    for (const r of rows) {
      const arr = by.get(r.group) ?? [];
      arr.push(r);
      by.set(r.group, arr);
    }
    return [...by.entries()];
  }, [rows]);

  const onSaved = (saved: BizConfigRow) => {
    setRows((prev) =>
      (prev ?? []).map((r) => (r.config_key === saved.config_key ? saved : r)),
    );
    setFlashKey(saved.config_key);
    setEdit(null);
  };

  const onReset = async (r: BizConfigRow) => {
    try {
      const saved = await api.bizConfigReset(r.config_key);
      onSaved(saved);
    } catch (e) {
      alert(`恢复默认失败：${e instanceof Error ? e.message : e}`);
    }
  };

  if (me === null) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-6">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="mt-6 h-64 w-full" />
      </div>
    );
  }
  if (!me.is_boss) return <Navigate to="/" replace />;

  return (
    <div className="flex-1">
      <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6">
        <PageHeader
          title="业务阈值配置"
          scope="仅本店铺生效，改后立即应用于看板与告警"
        />

        <div className="mt-5 space-y-6">
          {loadError ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-destructive">
                加载失败：{loadError}
              </CardContent>
            </Card>
          ) : rows === null ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            groups.map(([group, items]) => (
              <section key={group}>
                <h2 className="mb-2 px-1 text-sm font-semibold text-foreground-secondary">
                  {group}
                </h2>
                <Card>
                  <CardContent className="divide-y divide-border-shallow p-0">
                    {items.map((r) => (
                      <div
                        key={r.config_key}
                        className={cn(
                          "flex items-center gap-3 px-4 py-3 transition-colors",
                          flashKey === r.config_key && "bg-accent/60",
                        )}
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium text-foreground">
                              {r.label}
                            </span>
                            {r.is_overridden ? (
                              <Badge variant="success" className="text-[10px]">
                                已自定义
                              </Badge>
                            ) : (
                              <Badge variant="outline" className="text-[10px]">
                                默认
                              </Badge>
                            )}
                          </div>
                          {r.hint && (
                            <p className="mt-0.5 text-xs text-foreground-tertiary">
                              {r.hint}
                            </p>
                          )}
                        </div>
                        <div className="shrink-0 text-right">
                          <div className="tabnum text-sm font-semibold text-foreground">
                            {fmtNum(toDisplay(r, r.current_value))}
                            {r.unit && (
                              <span className="ml-0.5 text-xs font-normal text-foreground-tertiary">
                                {r.unit}
                              </span>
                            )}
                          </div>
                          {r.is_overridden && (
                            <div className="text-[10px] text-foreground-tertiary">
                              默认 {fmtNum(toDisplay(r, r.default_value))}
                              {r.unit}
                            </div>
                          )}
                        </div>
                        <div className="flex shrink-0 items-center gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-8"
                            aria-label="编辑"
                            onClick={() => setEdit(r)}
                          >
                            <Pencil className="size-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className={cn(
                              "size-8",
                              !r.is_overridden && "pointer-events-none opacity-30",
                            )}
                            aria-label="恢复默认"
                            title={r.is_overridden ? "恢复默认" : "已是默认值"}
                            onClick={() => r.is_overridden && onReset(r)}
                          >
                            <RotateCcw className="size-4" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </section>
            ))
          )}
        </div>
      </div>

      {edit && (
        <EditDialog row={edit} onClose={() => setEdit(null)} onSaved={onSaved} />
      )}
    </div>
  );
}

function EditDialog({
  row,
  onClose,
  onSaved,
}: {
  row: BizConfigRow;
  onClose: () => void;
  onSaved: (r: BizConfigRow) => void;
}) {
  const [val, setVal] = useState(String(fmtNum(toDisplay(row, row.current_value))));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 展示单位下的范围提示（%项把 min/max 也×100）。
  const lo = row.min != null ? toDisplay(row, row.min) : null;
  const hi = row.max != null ? toDisplay(row, row.max) : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const n = Number(val);
    if (!Number.isFinite(n)) {
      setError("请输入有效数字");
      return;
    }
    if ((lo != null && n < lo) || (hi != null && n > hi)) {
      setError(`须在 ${fmtNum(lo ?? 0)} ~ ${fmtNum(hi ?? 0)} 之间`);
      return;
    }
    if (row.type === "int" && !Number.isInteger(n)) {
      setError("须为整数");
      return;
    }
    setSubmitting(true);
    try {
      const saved = await api.bizConfigUpsert(row.config_key, fromDisplay(row, n));
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && !submitting && onClose()}>
      <DialogContent
        className="max-w-sm"
        onPointerDownOutside={(e) => submitting && e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{row.label}</DialogTitle>
          {row.hint && <DialogDescription>{row.hint}</DialogDescription>}
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="val">数值{row.unit ? `（${row.unit}）` : ""}</Label>
            <Input
              id="val"
              type="number"
              inputMode="decimal"
              step={row.type === "int" ? 1 : "any"}
              value={val}
              onChange={(e) => setVal(e.target.value)}
              autoFocus
              required
            />
            <p className="text-xs text-muted-foreground">
              {lo != null && hi != null ? `取值范围 ${fmtNum(lo)} ~ ${fmtNum(hi)}${row.unit ?? ""}，` : ""}
              默认 {fmtNum(toDisplay(row, row.default_value))}
              {row.unit}
            </p>
          </div>
          {error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              取消
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

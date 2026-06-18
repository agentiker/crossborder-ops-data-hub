import { NavLink } from "react-router-dom";
import { LayoutDashboard, MessagesSquare, ShieldCheck } from "lucide-react";
import type { Me } from "@/api";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  icon: typeof MessagesSquare;
  bossOnly?: boolean;
}

const ITEMS: NavItem[] = [
  { to: "/", label: "对话", icon: MessagesSquare },
  { to: "/board", label: "看板", icon: LayoutDashboard },
  { to: "/admin", label: "管理", icon: ShieldCheck, bossOnly: true },
];

interface Props {
  me: Me | null;
  onNavigate?: () => void; // 移动端点击后关抽屉
}

export function SideNav({ me, onNavigate }: Props) {
  const items = ITEMS.filter((it) => !it.bossOnly || me?.is_boss);
  return (
    <div className="flex h-full w-60 flex-col border-r bg-card">
      <div className="flex h-14 items-center gap-2 px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground text-sm font-bold">
          运
        </div>
        <span className="font-semibold tracking-tight">数据中枢</span>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-2">
        {items.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
              )
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>

      {me && (
        <div className="border-t px-4 py-3 text-xs">
          <div className="font-medium text-foreground">
            {me.is_boss ? "老板" : "运营"}
          </div>
          <div className="mt-0.5 truncate text-muted-foreground" title={me.scope_label}>
            {me.scope_label}
          </div>
        </div>
      )}
    </div>
  );
}

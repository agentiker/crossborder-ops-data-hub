import { LayoutDashboard, LogOut, MessagesSquare, ShieldCheck, UserRound } from "lucide-react";
import { NavLink } from "react-router-dom";
import type { Me } from "@/api";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";

const LOGOUT_PATH = "/board/auth/feishu/logout";

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
}

// 顶部栏：wordmark + 模式切换 tabs（对话/看板/管理）+ 主题/账户。
// 导航上移到顶部（StoreClaw 风），左侧空间留给对话页自己的会话列表。
export function TopBar({ me }: Props) {
  const items = ITEMS.filter((it) => !it.bossOnly || me?.is_boss);
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur sm:gap-5 sm:px-6">
      <span className="font-display text-lg font-semibold tracking-tight">数据中枢</span>

      <nav className="flex items-center gap-1">
        {items.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm transition-colors",
                isActive
                  ? "bg-accent font-medium text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )
            }
          >
            <Icon className="size-4" />
            <span className="hidden sm:inline">{label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-1">
        <ThemeToggle />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="账户">
              <UserRound />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>
              {me ? (me.is_boss ? "老板" : "运营") + " · " + me.role : "未登录"}
            </DropdownMenuLabel>
            {me && (
              <DropdownMenuItem disabled className="text-xs text-muted-foreground">
                范围：{me.scope_label}
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <a href={LOGOUT_PATH}>
                <LogOut className="size-4" />
                退出登录
              </a>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

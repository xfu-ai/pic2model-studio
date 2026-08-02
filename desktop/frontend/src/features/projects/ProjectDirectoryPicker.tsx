import { FolderOpen } from "@phosphor-icons/react";

/** Native folder selection is deliberately isolated from project API requests. */
export function ProjectDirectoryPicker({ actionLabel, disabled, onChoose }: { actionLabel: string; disabled: boolean; onChoose(): void }) {
  return <button className="primary project-launcher-action" disabled={disabled} onClick={onChoose}><FolderOpen size={18} />{actionLabel}</button>;
}

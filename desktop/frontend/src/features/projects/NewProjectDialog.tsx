import { Plus } from "@phosphor-icons/react";
import { ProjectDirectoryPicker } from "./ProjectDirectoryPicker";

export function NewProjectDialog({ name, busy, onNameChange, onCreate }: { name: string; busy: boolean; onNameChange(name: string): void; onCreate(): void }) {
  return <><label className="project-name-field">Project name<input value={name} maxLength={200} onChange={(event) => onNameChange(event.target.value)} disabled={busy} /></label><ProjectDirectoryPicker actionLabel="Choose folder and create" disabled={busy || !name.trim()} onChoose={onCreate} /><p className="project-launcher-help"><Plus size={16} />A project is created only after the native folder capability is accepted.</p></>;
}

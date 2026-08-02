import { Component, type ErrorInfo, type ReactNode } from "react";

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(_error: Error, _info: ErrorInfo) { /* diagnostics are added by the service layer, never raw stacks in UI */ }
  render() {
    if (this.state.failed) return <main className="app-recovery"><h1>工作台需要恢复</h1><p>界面未能完成加载。你的本地项目数据没有被更改。</p><button onClick={() => this.setState({ failed: false })}>重新尝试</button></main>;
    return this.props.children;
  }
}

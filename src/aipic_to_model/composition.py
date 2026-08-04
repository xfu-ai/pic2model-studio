"""The sole B01 composition root for local application services."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

from .agent.providers.deepseek import (
    DEEPSEEK_DEFAULT_MODEL,
    DEEPSEEK_PROFILE_REF,
    create_deepseek_credential_resolver,
)
from .api.dependencies import AppDependencies
from .application.agent_image_understanding import AgentImageUnderstandingService
from .application.app_state import AppStateService
from .application.archive_import import ProjectPackageService
from .application.assets import AssetService
from .application.b02_runtime import PersistentB02ToolRuntime
from .application.b02_tool_catalog import register_b02_tools
from .application.candidate_service import CandidateService
from .application.diagnostics import DiagnosticsService
from .application.events import EventService
from .application.generation_policy import GenerationPolicyResolver
from .application.host_capabilities import HostCapabilityStore
from .application.image_processing import ImageProcessingService
from .application.image_provider_routing import (
    CredentialProbeRoute,
    ImageProviderRoute,
    PrioritizedImageGenerationProvider,
)
from .application.jobs.configured_tripo import ConfiguredTripoJobHandler
from .application.jobs.external_image_handler import ExternalImageJobHandler
from .application.jobs.local_image_handler import LocalImageJobHandler
from .application.jobs.local_model_handler import LocalModelJobHandler
from .application.jobs.model_conversion import (
    ModelConversionService,
    ModelOptimizationService,
    ModelPackageService,
)
from .application.jobs.recovery_service import JobRecoveryService
from .application.jobs.runner import BackgroundJobRunner
from .application.jobs.triposr_handler import (
    LocalTripoSRJobHandler,
    Model3DGenerationJobRouter,
)
from .application.jobs.worker import ProductionJobWorker
from .application.jobs.z_image_handler import (
    ImageGenerationJobRouter,
    LocalZImageJobHandler,
)
from .application.local_image_processing import LocalImageProcessingService
from .application.local_tool_dispatch import LocalToolDispatcher
from .application.model_assets import ModelAssetService
from .application.multiview import MultiviewService
from .application.operations import OperationService
from .application.ports import (
    AssetRepositoryPort,
    EventRepositoryPort,
    FilesystemPort,
    PackageRepositoryPort,
    ProjectRepositoryPort,
    SelectionRepositoryPort,
    SettingsRepositoryPort,
    ToolRepositoryPort,
)
from .application.projects import ProjectService
from .application.prompt_service import PromptVersionService
from .application.selections import SelectionService
from .application.settings import SettingsService
from .application.tool_catalog import register_b01_tools
from .application.tools import ToolRegistry
from .domain.local_inference import default_local_provider_profiles
from .infrastructure.converters.controlled import (
    ApprovedConverterSettings,
    default_conversion_backends,
)
from .infrastructure.keyring_store import OSKeyringStore
from .infrastructure.local_inference import (
    CapabilityLocalProbe,
    LocalInferenceGate,
    LocalProviderMonitor,
    OllamaOpenAIProbe,
)
from .infrastructure.model_optimization import FastSimplificationGlbOptimizer
from .infrastructure.providers.config import (
    GEMINI_PROFILE,
    MESHY_PROFILE,
    TRIPO_PROFILE,
    CredentialResolver,
    GeminiSettings,
    MeshyImageSettings,
    TripoImageSettings,
    load_gemini_public_settings,
)
from .infrastructure.providers.controlled_e2e import (
    ControlledE2EFileTransferProvider,
    ControlledE2EImageProvider,
    ControlledE2ETripoProvider,
    ControlledE2EVisionProvider,
)
from .infrastructure.providers.credential_probe import (
    DeepSeekCredentialProbe,
    DeepSeekProbeSettings,
    GeminiCredentialProbe,
)
from .infrastructure.providers.gemini import GeminiVisionProvider
from .infrastructure.providers.meshy_image import MeshyTextToImageProvider
from .infrastructure.providers.tripo_http import TripoHttpSettings
from .infrastructure.providers.tripo_image import TripoTextToImageProvider
from .infrastructure.providers.z_image_turbo import ZImageTurboProvider
from .infrastructure.runtime import InfrastructureRuntime
from .infrastructure.sqlite.approval_repository import SqliteApprovalRepository
from .infrastructure.sqlite.candidate_repository import CandidateRepository
from .infrastructure.sqlite.job_repository import SqliteJobRepository
from .infrastructure.sqlite.model_repository import SqliteModelAssetRepository
from .infrastructure.sqlite.multiview_repository import MultiviewRepository
from .infrastructure.sqlite.prompt_repository import PromptVersionRepository
from .infrastructure.sqlite.repositories import (
    AssetRepository,
    EventRepository,
    PackageRepository,
    ProjectRepository,
    SelectionRepository,
    SettingsRepository,
    SqliteAppStateRepository,
    ToolRepository,
)
from .infrastructure.stable_diffusion_cpp import (
    STABLE_DIFFUSION_CPP_CAPABILITY,
    Z_IMAGE_DIFFUSION_CAPABILITY,
    Z_IMAGE_LLM_CAPABILITY,
    Z_IMAGE_VAE_CAPABILITY,
    StableDiffusionCppRunner,
    ZImageRuntimeConfig,
    resolve_environment_local_capability,
)
from .infrastructure.triposr_worker import (
    TRIPOSR_MODEL_CAPABILITY,
    TRIPOSR_RUNNER_CAPABILITY,
    TRIPOSR_WORKER_CAPABILITY,
    TripoSRRuntimeConfig,
    TripoSRWorkerRunner,
    resolve_environment_triposr_capability,
)


def compose_local_app(capabilities: HostCapabilityStore, app_db: Path) -> AppDependencies:
    controlled_e2e = os.environ.get("AIPIC_CONTROLLED_E2E") == "1"
    controlled_provider_failure = (
        os.environ.get("AIPIC_CONTROLLED_E2E_PROVIDER_FAILURE") == "1"
    )
    filesystem = cast(FilesystemPort, InfrastructureRuntime())
    operations = OperationService()
    assets = AssetService(cast(AssetRepositoryPort, AssetRepository()), filesystem, operations)
    selection_repository = cast(SelectionRepositoryPort, SelectionRepository())
    package_repository = cast(PackageRepositoryPort, PackageRepository())
    selections = SelectionService(selection_repository, filesystem, assets)
    projects = ProjectService(
        cast(ProjectRepositoryPort, ProjectRepository), filesystem, operations
    )
    registry = ToolRegistry(cast(ToolRepositoryPort, ToolRepository()), filesystem)
    register_b01_tools(registry, assets, selections, projects, selection_repository)
    jobs = SqliteJobRepository()
    approvals = SqliteApprovalRepository()
    prompt_repository = PromptVersionRepository()
    prompt_versions = PromptVersionService(assets, prompt_repository)
    multiview_repository = MultiviewRepository()
    multiview = MultiviewService(assets, selections, multiview_repository)
    model_assets = ModelAssetService(assets, SqliteModelAssetRepository())
    local_images = LocalImageProcessingService(assets)
    local_dispatcher = LocalToolDispatcher(
        assets,
        prompt_versions,
        ImageProcessingService(assets),
        local_images,
        multiview,
        model_assets,
    )
    optimization = ModelOptimizationService(assets, FastSimplificationGlbOptimizer())
    def configured_conversion_backends():
        # Read at conversion time so saving Settings takes effect for the next
        # job; no sidecar restart and no untrusted Tool argument is involved.
        converter_settings = SettingsRepository().get_app(app_db)
        blender = converter_settings.get("blender_path") or os.environ.get("AIPIC_TO_MODEL_BLENDER_PATH")
        return default_conversion_backends(
            ApprovedConverterSettings(
                blender_executable=Path(blender) if isinstance(blender, str) else None,
            )
        )

    local_model_handler = LocalModelJobHandler(
        jobs,
        model_assets,
        capabilities,
        ModelConversionService(assets, configured_conversion_backends),
        optimization,
        ModelPackageService(assets),
    )
    local_image_handler = LocalImageJobHandler(jobs, local_images)
    secret_store = OSKeyringStore()
    credentials = CredentialResolver(secret_store)
    gemini_settings = load_gemini_public_settings()
    resolved_gemini_settings = gemini_settings or GeminiSettings()
    deepseek_credential = create_deepseek_credential_resolver()
    gemini_vision = (
        ControlledE2EVisionProvider(fail=controlled_provider_failure)
        if controlled_e2e
        else GeminiVisionProvider(
            resolved_gemini_settings,
            credentials.callback(GEMINI_PROFILE),
        )
    )
    agent_image_understanding = AgentImageUnderstandingService(assets, gemini_vision)

    def sync_dispatcher(
        name: str,
        root: Path,
        project_id: str,
        arguments: dict[str, object],
        call_id: str,
    ):
        if name == "image.understand_for_agent":
            return agent_image_understanding.understand(
                root, project_id, arguments, call_id
            )
        return local_dispatcher(name, root, project_id, arguments, call_id)

    b02_runtime = PersistentB02ToolRuntime(
        jobs,
        approvals,
        sync_dispatcher,
        local_capability=lambda name: name != "model3d.optimize" or optimization.capability().available,
    )
    meshy_settings = MeshyImageSettings(
        base_url=os.environ.get("MESHY_BASE_URL", "https://api.meshy.ai"),
        allowed_image_hosts=frozenset(
            item.strip().lower()
            for item in os.environ.get("MESHY_IMAGE_HOSTS", "assets.meshy.ai").split(",")
            if item.strip()
        ),
    )
    meshy_images = (
        ControlledE2EImageProvider(fail=controlled_provider_failure)
        if controlled_e2e
        else MeshyTextToImageProvider(
            meshy_settings,
            credentials.callback(MESHY_PROFILE),
        )
    )
    tripo_hosts = (
        frozenset({"artifacts.fake.example"})
        if controlled_e2e
        else frozenset(
            item.strip().lower()
            for item in os.environ.get(
                "TRIPO_ARTIFACT_HOSTS",
                "*",
            ).split(",")
            if item.strip()
        )
    )
    tripo_settings = TripoHttpSettings(
        os.environ.get("TRIPO_BASE_URL", "https://openapi.tripo3d.ai"),
        tripo_hosts,
    )
    tripo_image_hosts = frozenset(
        item.strip().lower()
        for item in os.environ.get(
            "TRIPO_IMAGE_HOSTS",
            "tripo3d.ai,data.tripo3d.com",
        ).split(",")
        if item.strip()
    )
    tripo_images = (
        ControlledE2EImageProvider(fail=controlled_provider_failure)
        if controlled_e2e
        else TripoTextToImageProvider(
            TripoImageSettings(
                base_url=os.environ.get("TRIPO_BASE_URL", "https://openapi.tripo3d.ai"),
                advanced_image_base_url=os.environ.get(
                    "TRIPO_IMAGE_V2_BASE_URL", "https://api.tripo3d.ai"
                ),
                allowed_image_hosts=tripo_image_hosts,
            ),
            credentials.callback(TRIPO_PROFILE),
        )
    )
    image_provider = PrioritizedImageGenerationProvider(
        [
            ImageProviderRoute(
                profile=TRIPO_PROFILE,
                label="Tripo3D",
                channel="tripo",
                default_model=os.environ.get("TRIPO_IMAGE_MODEL", "seedream_v5"),
                modes=frozenset({"t2i", "i2i"}),
                provider=tripo_images,
                mode_models={
                    "i2i": os.environ.get(
                        "TRIPO_IMAGE_EDIT_MODEL", "gemini_3.1_flash_image_preview"
                    )
                },
            ),
            ImageProviderRoute(
                profile=MESHY_PROFILE,
                label="Meshy",
                channel="meshy",
                default_model=os.environ.get("MESHY_IMAGE_MODEL", "nano-banana"),
                modes=frozenset({"t2i", "i2i"}),
                provider=meshy_images,
            ),
        ],
        lambda: SettingsRepository().get_app(app_db),
        credential_probes=[
            CredentialProbeRoute(
                profile=GEMINI_PROFILE,
                label="Gemini",
                channel="google",
                default_model=resolved_gemini_settings.text_model,
                capabilities=("image_analysis", "prompt_rewrite"),
                provider=(
                    ControlledE2EImageProvider(fail=controlled_provider_failure)
                    if controlled_e2e
                    else GeminiCredentialProbe(
                        resolved_gemini_settings,
                        credentials.callback(GEMINI_PROFILE),
                    )
                ),
            ),
            CredentialProbeRoute(
                profile=DEEPSEEK_PROFILE_REF,
                label="DeepSeek Agent",
                channel="deepseek",
                default_model=os.environ.get("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL),
                capabilities=("agent_chat",),
                provider=(
                    ControlledE2EImageProvider(fail=controlled_provider_failure)
                    if controlled_e2e
                    else DeepSeekCredentialProbe(
                        DeepSeekProbeSettings(
                            base_url=os.environ.get(
                                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
                            )
                        ),
                        lambda: deepseek_credential(DEEPSEEK_PROFILE_REF),
                    )
                ),
            ),
        ],
    )
    candidates = CandidateService(assets, CandidateRepository())
    image_handler = ExternalImageJobHandler(
        jobs,
        assets,
        selections,
        multiview,
        multiview_repository,
        candidates,
        prompt_versions,
        gemini_vision,
        image_provider,
    )
    local_inference_gate = LocalInferenceGate()
    z_image_runner = StableDiffusionCppRunner(
        resolve_environment_local_capability,
        gate=local_inference_gate,
    )
    z_image_provider = ZImageTurboProvider(z_image_runner)
    z_image_handler = LocalZImageJobHandler(
        jobs,
        assets,
        candidates,
        z_image_provider,
    )
    tripo_handler = ConfiguredTripoJobHandler(
        jobs,
        assets,
        tripo_settings,
        credentials.callback(TRIPO_PROFILE),
        multiview_repository,
        provider=(
            ControlledE2ETripoProvider(fail=controlled_provider_failure)
            if controlled_e2e
            else None
        ),
        transfer_factory=ControlledE2EFileTransferProvider if controlled_e2e else None,
    )
    triposr_runner = TripoSRWorkerRunner(
        resolve_environment_triposr_capability,
        gate=local_inference_gate,
    )
    triposr_handler = LocalTripoSRJobHandler(
        jobs,
        assets,
        model_assets,
        triposr_runner,
    )
    profiles = default_local_provider_profiles()
    z_image_config = ZImageRuntimeConfig()
    triposr_config = TripoSRRuntimeConfig()

    def z_image_status(_capability_id: str):
        runtime = resolve_environment_local_capability(STABLE_DIFFUSION_CPP_CAPABILITY)
        models = [
            resolve_environment_local_capability(Z_IMAGE_DIFFUSION_CAPABILITY),
            resolve_environment_local_capability(Z_IMAGE_VAE_CAPABILITY),
            resolve_environment_local_capability(Z_IMAGE_LLM_CAPABILITY),
        ]
        return {
            "configured": runtime is not None,
            "available": z_image_runner.probe(z_image_config),
            "model_present": all(item is not None for item in models),
        }

    def triposr_status(_capability_id: str):
        runtime = resolve_environment_triposr_capability(TRIPOSR_WORKER_CAPABILITY)
        runner = resolve_environment_triposr_capability(TRIPOSR_RUNNER_CAPABILITY)
        model = resolve_environment_triposr_capability(TRIPOSR_MODEL_CAPABILITY)
        return {
            "configured": runtime is not None and runner is not None,
            "available": triposr_runner.probe(triposr_config),
            "model_present": model is not None,
        }

    local_provider_monitor = LocalProviderMonitor(
        profiles,
        {
            profiles[0].profile_id: OllamaOpenAIProbe(),
            profiles[1].profile_id: CapabilityLocalProbe(z_image_status),
            profiles[2].profile_id: CapabilityLocalProbe(triposr_status),
        },
    )
    registry.set_request_policy(
        GenerationPolicyResolver(
            lambda: SettingsRepository().get_app(app_db),
            local_provider_monitor,
            image_provider,
        )
    )
    register_b02_tools(registry, b02_runtime)
    handlers: dict[str, Callable[..., object]] = {
        name: local_model_handler
        for name in (
            "model3d.import_local",
            "model3d.render_preview",
            "model3d.convert",
            "model3d.optimize",
            "model3d.package",
        )
    }
    handlers["image.upscale_local"] = local_image_handler
    handlers.update(
        {
            name: image_handler.run
            for name in (
                "image.analyze_content",
                "image.analyze_style",
                "image.evaluate_3d_suitability",
                "prompt.rewrite",
                "image.transform",
                "image.generate_variants",
                "image.upscale",
                "image.remove_background",
                "image.inpaint_selection",
                "element.split",
                "element.export_transparent",
                "selection.auto_suggest_boxes",
                "multiview.generate",
                "multiview.detect_regions",
                "multiview.validate",
                "multiview.regenerate_view",
            )
        }
    )
    handlers["image.generate"] = ImageGenerationJobRouter(
        z_image_handler,
        image_handler.run,
    )
    handlers["model3d.generate"] = Model3DGenerationJobRouter(
        triposr_handler,
        tripo_handler.run,
    )
    handlers["model3d.download"] = tripo_handler.run
    job_worker = ProductionJobWorker(jobs, handlers)
    roots: dict[str, Path] = {}
    job_runner = BackgroundJobRunner(job_worker, roots)
    return AppDependencies(
        capabilities=capabilities,
        app_db=app_db,
        app_state=AppStateService(SqliteAppStateRepository()),
        events=EventService(cast(EventRepositoryPort, EventRepository())),
        projects=projects,
        prompt_versions=prompt_versions,
        assets=assets,
        selections=selections,
        multiview=multiview,
        packages=ProjectPackageService(
            package_repository,
            filesystem,
            operations,
        ),
        registry=registry,
        settings=SettingsService(cast(SettingsRepositoryPort, SettingsRepository()), filesystem),
        secret_store=secret_store,
        diagnostics=DiagnosticsService(filesystem),
        jobs=jobs,
        job_recovery=JobRecoveryService(jobs),
        b02_runtime=b02_runtime,
        job_worker=job_worker,
        roots=roots,
        job_runner=job_runner,
        image_provider_monitor=image_provider,
        local_provider_monitor=local_provider_monitor,
    )

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2025-11-07

### Added
- GPU packages (`nvidia-cublas`, `nvidia-cudnn`) now included as default dependencies
- Installation script `install_with_gpu.sh` for easy pipx installation with GPU support
- Comprehensive installation guide in README.md for pipx and git clone methods
- Support for installing from GitHub via `pipx install git+https://github.com/draeician/local_transcribe.git`

### Fixed
- Fixed subcommand detection in smart callback - `lt doctor`, `lt status`, etc. now work correctly
- Fixed doctor command to properly detect `nvidia.cu13` (CUDA 13 version of cublas)
- Updated entry point to use `main()` function for better pipx compatibility

### Changed
- GPU packages moved from optional dependencies to default dependencies
- Updated README.md with detailed pipx installation instructions
- Updated START_HERE.md with pipx installation options
- Updated requirements.txt to include CUDA packages

## [0.1.1] - 2025-11-07

### Fixed
- Fixed subcommand execution when using smart detection callback
- Subcommands (doctor, status, etc.) now work correctly with `invoke_without_command=True`

## [0.1.0] - 2025-11-07

### Added
- Initial library-first refactoring
- Unified CLI with `lt` command
- Smart detection for URLs vs file paths
- Default values for common options (model, device, compute_type, output-dir)
- Batch processing with resume capability
- Status tracking and reconciliation
- Environment diagnostics (`lt doctor`)


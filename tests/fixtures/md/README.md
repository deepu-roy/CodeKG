# MyApp

Overview of MyApp.

## Installation

Run `npm install` to set up dependencies.

## Architecture

The app uses `UserService` for user management and `CandidateService` for hiring.

### Controllers

Controllers delegate to services. `UserController` handles HTTP routes.

### Services

`UserService` and `CandidateService` form the core business logic.

## See Also

See [docs/api.md](docs/api.md) for API reference.

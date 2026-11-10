// Only for messages authored by the UI. Never wrap raw API/provider responses,
// credentials, drafts or capability tokens in this error type.
export class UserMessageError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'UserMessageError'
  }
}

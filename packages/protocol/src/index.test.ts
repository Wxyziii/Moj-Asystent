import { describe, expect, it } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import cases from "../fixtures/contract-cases.json";
import schema from "../schema/protocol-v1.json";
import { createClientHello, parseProtocolEvent } from "./index";

describe("shared protocol acceptance corpus", () => {
  const ajv = new Ajv2020({ strict: true });
  addFormats(ajv);
  const validateSchema = ajv.compile(schema);

  it.each(cases)("$name", ({ value, valid }) => {
    expect(validateSchema(value)).toBe(valid);
    expect(parseProtocolEvent(value) !== null).toBe(valid);
  });
  it("serializes the desktop hello to the shared contract", () => {
    const hello = createClientHello("desktop");
    expect(parseProtocolEvent(JSON.parse(JSON.stringify(hello)))).toEqual(
      hello,
    );
  });
});
